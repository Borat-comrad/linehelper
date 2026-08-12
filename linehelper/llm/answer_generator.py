"""Read-only RAG answer generation over semantic memory and local Ollama."""

from __future__ import annotations

import os
import re
import time
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from linehelper.catalogs.chat import CatalogChatOutcome, CatalogChatService
from linehelper.catalogs.models import CatalogProbeDiagnostics, CatalogSource
from linehelper.catalogs.navigation import CatalogOccurrenceNavigation
from linehelper.llm.ollama_client import OllamaClient, OllamaError
from linehelper.rag.answer_contract import (
    AnswerContractBuilder,
    AnswerContractValidator,
    GroundedAnswerRenderer,
)
from linehelper.rag.prompt_builder import build_rag_prompt
from linehelper.rag.conversation_resolver import (
    ConversationContext,
    ConversationResolver,
    ConversationTurn,
    PendingClarification,
    assistant_history_metadata,
    conversation_diagnostics,
    pending_clarification_from_plan,
)
from linehelper.rag.context_composer import (
    DEFAULT_CONTEXT_MAX_CHARS,
    ContextComposer,
    ContextPlanner,
)
from linehelper.rag.evidence_assessor import (
    EvidenceAssessor,
    EvidencePlanner,
)
from linehelper.rag.retriever import (
    RetrievedChunk,
    RetrievalPlanner,
    SemanticRetriever,
)

if TYPE_CHECKING:
    from linehelper.rag.query_analyzer import QueryPlan


DEFAULT_RETRIEVAL_LIMIT = 5
DEFAULT_CANDIDATE_LIMIT = 30
DEFAULT_CONTEXT_LIMIT = 3
DEFAULT_CONTEXT_SCORE_RATIO = 0.65
MIN_GENERIC_CONTEXT_SCORE = 35.0
LOGGER = logging.getLogger(__name__)
NO_ANSWER_MESSAGE = (
    "В базе знаний Serviceline нет точного ответа на этот вопрос. "
    "Похоже, вопрос не относится к корпоративным регламентам, инструкциям, "
    "оргструктуре или документообороту."
)
KP_COMMERCIAL_OFFER_MESSAGE = (
    "В базе знаний нет отдельной полной инструкции по КП как коммерческому предложению. "
    "В найденных источниках могут встречаться упоминания коммерческого предложения как продукта "
    "отделов продаж, но этого недостаточно для полного ответа."
)

ANCHOR_TERMS: dict[str, tuple[str, ...]] = {
    "отпуск": ("отпуск", "отпуска", "отпуске", "отпусков", "отпускной"),
    "зрс": ("зрс", "завершенная работа сотрудника"),
    "цкп": ("цкп", "ценный конечный продукт"),
    "задачи": (
        "задача",
        "задачу",
        "задачи",
        "задач",
        "взять задачу в работу",
        "работа с задачами",
    ),
    "контроль": (
        "взять под контроль",
        "под контроль",
        "контроль",
        "контроля",
        "контролем",
    ),
    "командировка": ("командировка", "командировки", "командировку"),
    "договор": ("договор", "договора", "договоров", "договоре"),
    "распоряжение": (
        "распоряжение",
        "распоряжения",
        "распоряжений",
        "распоряжением",
    ),
}

COMPARISON_TRIGGERS: tuple[str, ...] = (
    "это то же самое",
    "то же самое",
    "это отпуск",
    "чем отличается",
    "одно и то же",
)

INTENT_ANCHOR_TERMS: dict[str, tuple[str, ...]] = {
    "company_identity": (
        "ип-0002",
        "цели и замыслы",
        "цель компании",
        "основная цель компании",
        "ип-0003",
        "цкп serviceline",
        "ценный конечный продукт",
        "комплексная услуга",
    ),
    "document_flow": (
        "ип-0006",
        "документооборот",
        "1с до",
        "1с документооборот",
        "согласование",
        "согласования",
        "инструкция согласования",
    ),
    "ckp": (
        "ип-0003",
        "цкп serviceline",
        "цкп",
        "ценный конечный продукт",
    ),
}

INTENT_PREFERRED_TERMS: dict[str, tuple[str, ...]] = {
    "company_identity": (
        "ип-0002",
        "цели и замыслы",
        "ип-0003",
        "цкп serviceline",
        "цель компании",
        "основная цель компании",
    ),
    "document_flow": (
        "ип-0006",
        "документооборот",
        "1с до",
        "согласования",
        "инструкция согласования",
    ),
    "ckp": (
        "ип-0003",
        "цкп serviceline",
        "ценный конечный продукт",
    ),
}

ORGANIZATION_STRUCTURED_DOC_TYPES = frozenset(
    {
        "organization_overview",
        "organization_unit",
        "employee_role",
        "responsibility_route",
        "organization_status",
        "organization_vacancy",
        "role_combination",
    }
)
ORGANIZATION_STRUCTURED_SOURCE_TITLE = "bvr_company_structure_instruction_v2 (2).txt"
ORGANIZATION_KNOWLEDGE_DOMAIN = "organization_structure"
ORGANIZATION_INTENTS = frozenset({"org_structure", "roles_responsibility"})
UNIT_IDENTIFIER_RE = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё])\d{1,2}[А-Яа-яA-Za-z]?(?![0-9A-Za-zА-Яа-яЁё])")
ORGANIZATION_HIGH_SIGNAL_IDENTIFIER_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])(?:KRONES|KHS|HEUFT|SIDEL|SMI|IMETA|EFES|ЭФЕС)(?![0-9A-Za-zА-Яа-яЁё])",
    re.IGNORECASE,
)
LEADERSHIP_TERMS = (
    "кто руководит",
    "кто главный",
    "начальник",
    "руководитель",
    "руководител",
)
CONTACT_ROUTE_TERMS = (
    "к кому обратиться",
    "кому направить",
    "кто отвечает",
    "контакт",
    "телефон",
    "написать",
)

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
_KP_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])кп(?![0-9A-Za-zА-Яа-яЁё])",
    re.IGNORECASE,
)
_TRAILING_SOURCE_BLOCK_RE = re.compile(
    r"(?:\n\s*){1,}(?:источники ответа|источники|источник)\s*:\s*(?:\n|.)*\Z",
    re.IGNORECASE,
)
_GENERIC_CONTEXT_STOP_WORDS = frozenset(
    {
        "без",
        "бега",
        "было",
        "быть",
        "вас",
        "все",
        "для",
        "делать",
        "если",
        "есть",
        "как",
        "какие",
        "какой",
        "когда",
        "мне",
        "можно",
        "надо",
        "нужно",
        "новая",
        "нового",
        "новое",
        "новой",
        "новый",
        "после",
        "получить",
        "почему",
        "работа",
        "работать",
        "работы",
        "сделать",
        "сколько",
        "такое",
        "хочу",
        "что",
        "чтобы",
    }
)

SYSTEM_MESSAGE = (
    "Ты корпоративный помощник Serviceline. Отвечай только на русском языке. "
    "Отвечай только на основе предоставленных источников. Если в источниках "
    "недостаточно данных, честно скажи, что данных недостаточно. Не выдумывай "
    "факты. Не используй английский, китайский или корейский язык, если "
    "пользователь явно не просит перевод. Отвечай именно на вопрос пользователя, "
    "а не на похожую общую тему. Если пользователь спрашивает, что делать, "
    "давай практические шаги только из источников. Игнорируй источники, которые "
    "не относятся к предмету вопроса, и не используй случайное совпадение слов "
    "как основание для ответа. Если источники дают только частичный ответ, "
    "прямо скажи, чего в них нет. Если вопрос сравнивает два понятия или "
    "действия, сначала ответь да или нет, затем кратко объясни различие по "
    "источникам и не подменяй сравнение инструкцией по одной стороне. Не добавляй "
    "в ответ раздел Источники и не перечисляй названия документов: интерфейс покажет "
    "источники отдельно."
)


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Return assistant answer for chat messages."""


class QueryAnalyzerClient(Protocol):
    def analyze(self, question: str) -> QueryPlan:
        """Return a structured retrieval plan for a user question."""


class InteractionLoggerClient(Protocol):
    last_error: str | None

    def record_answer(
        self,
        result: "RagAnswer",
        *,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """Queue one completed answer for best-effort persistence."""


class CatalogChatClient(Protocol):
    def lookup(self, question: str) -> CatalogChatOutcome | None:
        """Return a deterministic catalog answer, or None for an unrelated query."""

    def probe_natural_language(
        self,
        question: str,
    ) -> CatalogProbeDiagnostics:
        """Return only conservative natural-language catalog matches."""


@dataclass(frozen=True)
class RagSource:
    title: str
    source: str
    section: str | None
    page: int | None
    logical_unit_title: str | None
    score: float | None
    matched_excerpt: str


@dataclass(frozen=True)
class QueryIntent:
    name: str
    anchor_terms: tuple[str, ...] = ()
    preferred_terms: tuple[str, ...] = ()
    require_preferred_context: bool = False
    min_context_score: float = MIN_GENERIC_CONTEXT_SCORE


@dataclass(frozen=True)
class RagAnswer:
    question: str
    answer: str
    model: str
    sources: list[RagSource]
    chunks_used: int
    prompt_length: int
    elapsed_seconds: float
    retrieval_limit: int
    candidate_limit: int
    context_limit: int
    context_score_ratio: float
    diagnostic_candidates: list[RagSource]
    response_kind: str = "answer"
    query_plan: dict[str, Any] | None = None
    resolved_question: str | None = None
    conversation: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    answer_contract: dict[str, Any] | None = None
    contract_validation: dict[str, Any] | None = None
    final_answer_sections: list[str] | None = None
    catalog: dict[str, Any] | None = None
    catalog_sources: tuple[CatalogSource, ...] = ()
    catalog_navigation: tuple[CatalogOccurrenceNavigation, ...] = ()
    interaction_id: str | None = None
    analytics_logged: bool = False
    analytics_error: str | None = None


def rag_answer_history_metadata(result: RagAnswer) -> dict[str, Any]:
    """Return compact structured state to store with an assistant chat turn."""
    return assistant_history_metadata(
        conversation=result.conversation,
        query_plan=result.query_plan,
        response_kind=result.response_kind,
    )


@dataclass(frozen=True)
class QueryAnalysisResult:
    plan: QueryPlan | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class RetrievalExecution:
    chunks: list[RetrievedChunk]
    diagnostics: dict[str, Any] | None = None
    native: bool = False


class RagAnswerError(RuntimeError):
    """Raised when the read-only RAG answer flow cannot complete."""


class RagAnswerGenerator:
    """Connect semantic retrieval, prompt building and local Ollama chat."""

    def __init__(
        self,
        *,
        retriever: SemanticRetriever | None = None,
        llm_client: ChatClient | None = None,
        db_path: Path | None = None,
        catalog_db_path: Path | None = None,
        catalog_source_root: Path | None = None,
        context_limit: int | None = None,
        context_score_ratio: float | None = None,
        context_char_budget: int | None = None,
        query_analyzer: QueryAnalyzerClient | None = None,
        conversation_resolver: ConversationResolver | None = None,
        interaction_logger: InteractionLoggerClient | None = None,
        catalog_chat: CatalogChatClient | None = None,
    ) -> None:
        self.retriever = retriever or SemanticRetriever(db_path)
        self.llm_client = llm_client or OllamaClient()
        self.query_analyzer = query_analyzer
        self.conversation_resolver = conversation_resolver or ConversationResolver()
        self.interaction_logger = interaction_logger
        self.catalog_chat = catalog_chat or (
            CatalogChatService(
                catalog_db_path,
                source_root=catalog_source_root,
            )
            if catalog_db_path is not None
            else None
        )
        self.context_limit = max(
            1,
            context_limit
            if context_limit is not None
            else _env_int("RAG_CONTEXT_LIMIT", DEFAULT_CONTEXT_LIMIT),
        )
        raw_score_ratio = (
            context_score_ratio
            if context_score_ratio is not None
            else _env_float("RAG_CONTEXT_SCORE_RATIO", DEFAULT_CONTEXT_SCORE_RATIO)
        )
        self.context_score_ratio = max(0.0, min(raw_score_ratio, 1.0))
        self.context_char_budget = max(
            1,
            context_char_budget
            if context_char_budget is not None
            else _env_int("RAG_CONTEXT_MAX_CHARS", DEFAULT_CONTEXT_MAX_CHARS),
        )

    def answer(
        self,
        question: str,
        *,
        history: Sequence[ConversationTurn | Mapping[str, Any]] | None = None,
        conversation_context: ConversationContext | None = None,
        retrieval_limit: int = DEFAULT_RETRIEVAL_LIMIT,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> RagAnswer:
        """Return the unchanged RAG result after a best-effort logging side effect."""
        result = self._answer_without_logging(
            question,
            history=history,
            conversation_context=conversation_context,
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
        )
        return self._record_completed_answer(
            result,
            session_id=session_id,
            user_id=user_id,
        )

    def _answer_without_logging(
        self,
        question: str,
        *,
        history: Sequence[ConversationTurn | Mapping[str, Any]] | None = None,
        conversation_context: ConversationContext | None = None,
        retrieval_limit: int = DEFAULT_RETRIEVAL_LIMIT,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    ) -> RagAnswer:
        """Build an answer using semantic memory as read-only context."""
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("question must not be empty")

        started_at = time.monotonic()
        catalog_probe = None
        if self.catalog_chat is not None:
            catalog_outcome = self.catalog_chat.lookup(clean_question)
            if catalog_outcome is not None:
                return self._catalog_answer(
                    clean_question,
                    catalog_outcome,
                    started_at=started_at,
                    retrieval_limit=retrieval_limit,
                    candidate_limit=candidate_limit,
                )
            probe = getattr(self.catalog_chat, "probe_natural_language", None)
            if callable(probe):
                catalog_probe = probe(clean_question)
                if (
                    catalog_probe.outcome is not None
                    and catalog_probe.source_route == "catalog"
                ):
                    return self._catalog_answer(
                        clean_question,
                        catalog_probe.outcome,
                        started_at=started_at,
                        retrieval_limit=retrieval_limit,
                        candidate_limit=candidate_limit,
                        probe=catalog_probe,
                    )
        resolved = self.conversation_resolver.resolve(
            clean_question,
            history=history,
            conversation_context=conversation_context,
        )
        resolved_question = resolved.resolved_question

        if (
            resolved.resolution_kind == "unresolved_follow_up"
            and resolved.pending_clarification_before is not None
        ):
            pending = resolved.pending_clarification_before
            query_plan_diagnostics = _pending_clarification_diagnostics(pending)
            return RagAnswer(
                question=clean_question,
                answer=pending.plan.question or clean_question,
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
                diagnostic_candidates=[],
                response_kind="clarification",
                query_plan=query_plan_diagnostics,
                resolved_question=resolved_question,
                conversation=conversation_diagnostics(
                    resolved,
                    pending_after=pending,
                ),
            )

        query_analysis = self._analyze_query(resolved_question)
        query_plan = query_analysis.plan
        if catalog_probe is not None and query_plan is not None:
            normalized_subject = str(query_plan.subject or "").strip()
            if normalized_subject:
                requirement_subject = _catalog_requirement_subject(
                    catalog_probe.query,
                    normalized_subject,
                )
                catalog_probe = replace(
                    catalog_probe,
                    resolved_requirements=tuple(
                        replace(requirement, subject=requirement_subject)
                        for requirement in catalog_probe.resolved_requirements
                    ),
                )
        query_plan_diagnostics = _with_catalog_probe_diagnostics(
            query_analysis.diagnostics,
            catalog_probe,
            corporate_evidence_available=False,
        )

        clarification = _query_plan_clarification(query_plan)
        if clarification is not None:
            pending_after = pending_clarification_from_plan(
                resolved_question,
                query_plan.clarification if query_plan is not None else None,
            )
            return RagAnswer(
                question=clean_question,
                answer=clarification,
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
                diagnostic_candidates=[],
                response_kind="clarification",
                query_plan=query_plan_diagnostics,
                resolved_question=resolved_question,
                conversation=conversation_diagnostics(
                    resolved,
                    pending_after=pending_after,
                ),
            )

        conversation_result = conversation_diagnostics(
            resolved,
            pending_after=None,
        )
        intent = _runtime_query_intent(resolved_question, query_plan)
        retrieval_execution = self._retrieve_with_query_plan(
            resolved_question,
            original_question=clean_question,
            query_plan=query_plan,
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
        )
        chunks = retrieval_execution.chunks
        retrieval_diagnostics = retrieval_execution.diagnostics
        if not retrieval_execution.native:
            chunks = _boost_organization_chunks(
                chunks,
                resolved_question,
                intent=intent,
            )
        if intent.name == "kp_commercial_offer":
            return RagAnswer(
                question=clean_question,
                answer=KP_COMMERCIAL_OFFER_MESSAGE,
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
                diagnostic_candidates=[
                    _source_from_chunk(chunk)
                    for chunk in chunks
                    if not _chunk_contains_any_term(chunk, INTENT_ANCHOR_TERMS["ckp"])
                ],
                response_kind="partial_answer",
                query_plan=query_plan_diagnostics,
                resolved_question=resolved_question,
                conversation=conversation_result,
                retrieval=retrieval_diagnostics,
            )
        if intent.name == "comparison":
            chunks = sorted(
                _dedupe_chunks(
                    [
                        *chunks,
                        *self._retrieve_comparison_candidates(
                            resolved_question,
                            candidate_limit=candidate_limit,
                        ),
                    ]
                ),
                key=_chunk_score,
                reverse=True,
            )
        base_context_chunks = select_context_chunks(
            resolved_question,
            chunks,
            intent=intent,
            max_chunks=self.context_limit,
            score_ratio=self.context_score_ratio,
        )
        context_plan = ContextPlanner().build(
            query_plan=query_plan,
            configured_max_chunks=self.context_limit,
            max_context_chars=self.context_char_budget,
            score_ratio=self.context_score_ratio,
        )
        context_selection = ContextComposer().compose(
            resolved_question,
            chunks,
            plan=context_plan,
            base_selection=base_context_chunks,
            candidate_provenance=(
                retrieval_diagnostics.get("candidate_provenance", [])
                if isinstance(retrieval_diagnostics, dict)
                else []
            ),
        )
        selected_context_chunks = list(context_selection.selected)
        context_gate_chunks = (
            selected_context_chunks
            if has_sufficient_context(
                resolved_question,
                selected_context_chunks,
                intent=intent,
            )
            else []
        )
        context_diagnostics = context_selection.to_dict(
            applied_chunks=context_gate_chunks,
        )
        evidence_plan = EvidencePlanner().build(
            resolved_question,
            query_plan=query_plan,
            context_plan=context_plan,
        )
        evidence_decision = EvidenceAssessor().assess(
            resolved_question,
            context_gate_chunks,
            plan=evidence_plan,
        )
        evidence_diagnostics = evidence_decision.to_dict()
        original_supporting_chunks = list(evidence_decision.supporting_chunks)
        evidence_chunks = list(original_supporting_chunks)
        domain_consistency = _mixed_catalog_domain_consistency(
            catalog_probe,
            evidence_chunks,
        )
        if domain_consistency["checked"] and not domain_consistency["passed"]:
            evidence_chunks = []
            evidence_diagnostics = {
                **evidence_diagnostics,
                "domain_consistency_checked": True,
                "domain_consistency_passed": False,
                "domain_consistency_reason": domain_consistency["reason"],
                "rejected_supporting_chunk_ids": list(
                    domain_consistency["rejected_chunk_ids"]
                ),
                "supporting_chunk_ids": [],
                "non_supporting_chunk_ids": list(
                    dict.fromkeys(
                        [
                            *evidence_diagnostics.get("non_supporting_chunk_ids", []),
                            *domain_consistency["rejected_chunk_ids"],
                        ]
                    )
                ),
            }
        elif domain_consistency["checked"]:
            evidence_chunks = list(domain_consistency["accepted_chunks"])
            evidence_diagnostics = {
                **evidence_diagnostics,
                "domain_consistency_checked": True,
                "domain_consistency_passed": True,
                "domain_consistency_reason": domain_consistency["reason"],
                "domain_inconsistent_chunk_ids": list(
                    domain_consistency["rejected_chunk_ids"]
                ),
                "supporting_chunk_ids": [
                    _chunk_diagnostic_id(chunk) for chunk in evidence_chunks
                ],
            }
        corporate_evidence_rejected = bool(
            domain_consistency["checked"] and not domain_consistency["passed"]
        )
        answer_contract = AnswerContractBuilder().build(evidence_decision)
        answer_contract_diagnostics = answer_contract.to_dict()
        sources = [_source_from_chunk(chunk) for chunk in evidence_chunks]
        diagnostic_candidates = [
            _source_from_chunk(chunk)
            for chunk in chunks
            if chunk not in evidence_chunks
        ]

        if evidence_decision.mode == "insufficient_evidence" or corporate_evidence_rejected:
            if catalog_probe is not None and catalog_probe.outcome is not None:
                return self._catalog_answer(
                    clean_question,
                    catalog_probe.outcome,
                    started_at=started_at,
                    retrieval_limit=retrieval_limit,
                    candidate_limit=candidate_limit,
                    probe=catalog_probe,
                    corporate_evidence_available=False,
                    corporate_diagnostics={
                        "query_plan": query_plan_diagnostics,
                        "retrieval": retrieval_diagnostics,
                        "context": context_diagnostics,
                        "evidence": evidence_diagnostics,
                        "answer_contract": answer_contract_diagnostics,
                        "contract_validation": None,
                    },
                )
            contract_validation = AnswerContractValidator().validate(
                "",
                answer_contract,
            )
            rendered = GroundedAnswerRenderer().render(
                "",
                answer_contract,
                contract_validation,
            )
            return RagAnswer(
                question=clean_question,
                answer=rendered.answer,
                model=self.llm_client.model,
                sources=[],
                chunks_used=0,
                prompt_length=0,
                elapsed_seconds=round(time.monotonic() - started_at, 3),
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                context_limit=self.context_limit,
                context_score_ratio=self.context_score_ratio,
                diagnostic_candidates=diagnostic_candidates,
                response_kind="no_answer",
                query_plan=query_plan_diagnostics,
                resolved_question=resolved_question,
                conversation=conversation_result,
                retrieval=retrieval_diagnostics,
                context=context_diagnostics,
                evidence=evidence_diagnostics,
                answer_contract=answer_contract_diagnostics,
                contract_validation=contract_validation.to_dict(),
                final_answer_sections=list(
                    rendered.final_answer_sections
                ),
            )

        prompt = build_rag_prompt(
            resolved_question,
            evidence_chunks,
            max_context_chars=self.context_char_budget,
            answer_contract=answer_contract.to_prompt_dict(),
        )
        messages = [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]

        try:
            answer_text = self.llm_client.chat(messages)
        except OllamaError as exc:
            raise RagAnswerError(str(exc)) from exc

        contract_validation = AnswerContractValidator().validate(
            answer_text,
            answer_contract,
        )
        rendered = GroundedAnswerRenderer().render(
            answer_text,
            answer_contract,
            contract_validation,
        )

        if (
            catalog_probe is not None
            and catalog_probe.outcome is not None
            and catalog_probe.source_route == "mixed"
        ):
            catalog_answer = self._catalog_answer(
                clean_question,
                catalog_probe.outcome,
                started_at=started_at,
                retrieval_limit=retrieval_limit,
                candidate_limit=candidate_limit,
                probe=catalog_probe,
                corporate_evidence_available=True,
            )
            return replace(
                catalog_answer,
                answer=(
                    f"{catalog_answer.answer}\n\n"
                    f"Корпоративная база знаний:\n{rendered.answer}"
                ),
                model=self.llm_client.model,
                sources=sources,
                chunks_used=len(evidence_chunks),
                prompt_length=len(prompt),
                diagnostic_candidates=diagnostic_candidates,
                response_kind="mixed_answer",
                query_plan=_with_catalog_probe_diagnostics(
                    query_plan_diagnostics,
                    catalog_probe,
                    corporate_evidence_available=True,
                    source_route="mixed",
                ),
                resolved_question=resolved_question,
                conversation=conversation_result,
                retrieval={
                    "retrieval_stages": [
                        catalog_probe.outcome.route,
                        *(retrieval_diagnostics or {}).get("retrieval_stages", []),
                    ],
                    "corporate": retrieval_diagnostics,
                },
                context=context_diagnostics,
                evidence=evidence_diagnostics,
                answer_contract=answer_contract_diagnostics,
                contract_validation=contract_validation.to_dict(),
                final_answer_sections=[
                    "catalog_candidates",
                    *rendered.final_answer_sections,
                ],
            )

        corporate_answer = RagAnswer(
            question=clean_question,
            answer=rendered.answer,
            model=self.llm_client.model,
            sources=sources,
            chunks_used=len(evidence_chunks),
            prompt_length=len(prompt),
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
            context_limit=self.context_limit,
            context_score_ratio=self.context_score_ratio,
            diagnostic_candidates=diagnostic_candidates,
            response_kind=(
                "partial_answer"
                if evidence_decision.mode == "partial_answer"
                else "answer"
            ),
            query_plan=_with_catalog_probe_diagnostics(
                query_plan_diagnostics,
                catalog_probe,
                corporate_evidence_available=True,
                source_route="corporate",
            ),
            resolved_question=resolved_question,
            conversation=conversation_result,
            retrieval=retrieval_diagnostics,
            context=context_diagnostics,
            evidence=evidence_diagnostics,
            answer_contract=answer_contract_diagnostics,
            contract_validation=contract_validation.to_dict(),
            final_answer_sections=list(rendered.final_answer_sections),
        )
        if (
            catalog_probe is not None
            and catalog_probe.source_route == "mixed"
            and catalog_probe.outcome is None
        ):
            return replace(
                corporate_answer,
                answer=(
                    f"{corporate_answer.answer}\n\n"
                    "В Catalog Store не найдено подтверждённых сведений "
                    "о составе или деталях этого объекта."
                ),
                response_kind="partial_mixed_answer",
                query_plan=_with_catalog_probe_diagnostics(
                    query_plan_diagnostics,
                    catalog_probe,
                    corporate_evidence_available=True,
                    source_route="mixed",
                    corporate_requirement_status="found",
                    answer_mode="partial_mixed",
                ),
                final_answer_sections=[
                    *rendered.final_answer_sections,
                    "catalog_requirement_not_found",
                ],
            )
        return corporate_answer

    def _catalog_answer(
        self,
        question: str,
        outcome: CatalogChatOutcome,
        *,
        started_at: float,
        retrieval_limit: int,
        candidate_limit: int,
        probe: CatalogProbeDiagnostics | None = None,
        corporate_evidence_available: bool = False,
        corporate_diagnostics: dict[str, Any] | None = None,
    ) -> RagAnswer:
        candidate_routes = {
            "catalog_text_search",
            "catalog_code_search",
            "catalog_natural_search",
        }
        structured_routes = {
            "catalog_assembly_exact",
            "catalog_assembly_contents",
            "catalog_bom_position",
            "catalog_part_exact",
            "catalog_entity_ambiguity",
        }
        is_candidate_search = outcome.route in candidate_routes
        if is_candidate_search:
            response_kind = {
                "candidates": "catalog_candidates",
                "no_candidates": "catalog_search_not_found",
                "clarification": "catalog_search_clarification",
                "unavailable": "catalog_unavailable",
            }.get(outcome.status, "catalog_unavailable")
            intent = outcome.route
            requested_fact_type = "catalog_candidates"
            matched_signals = [
                "deterministic_part_code_search"
                if outcome.route == "catalog_code_search"
                else "explicit_catalog_text_search"
            ]
            subject = outcome.search_query
        elif outcome.route in structured_routes:
            response_kind = {
                "found": outcome.route,
                "ambiguous": "catalog_entity_ambiguity",
                "not_found": "catalog_not_found",
                "unavailable": "catalog_unavailable",
            }.get(outcome.status, "catalog_unavailable")
            intent = outcome.route
            requested_fact_type = {
                "catalog_assembly_exact": "catalog_assembly",
                "catalog_assembly_contents": "catalog_assembly_contents",
                "catalog_bom_position": "catalog_bom_position",
                "catalog_part_exact": "part_number",
                "catalog_entity_ambiguity": "catalog_entity_disambiguation",
            }[outcome.route]
            matched_signals = ["explicit_structured_catalog_intent"]
            subject = outcome.identifier
        else:
            response_kind = {
                "found": "catalog_exact",
                "not_found": "catalog_not_found",
                "unavailable": "catalog_unavailable",
            }.get(outcome.status, "catalog_unavailable")
            intent = "catalog_exact_lookup"
            requested_fact_type = "part_number"
            matched_signals = ["explicit_part_number_lookup"]
            subject = outcome.identifier
        is_mixed_probe = probe is not None and probe.source_route == "mixed"
        corporate_requirement_status = (
            "found"
            if corporate_evidence_available
            else (
                "not_found"
                if is_mixed_probe and corporate_diagnostics is not None
                else "not_requested"
            )
        )
        answer_mode = (
            "full_mixed"
            if is_mixed_probe and corporate_evidence_available
            else "partial_mixed"
            if is_mixed_probe and corporate_requirement_status == "not_found"
            else "catalog"
        )
        answer = outcome.answer
        if answer_mode == "partial_mixed":
            answer += (
                "\n\nВ доступной базе документов не найдено подтверждённой "
                "процедуры обслуживания этого устройства."
            )
            response_kind = "partial_mixed_answer"

        corporate_query_plan = (
            corporate_diagnostics.get("query_plan")
            if isinstance(corporate_diagnostics, dict)
            else None
        )
        diagnostics = dict(
            corporate_query_plan if isinstance(corporate_query_plan, dict) else {}
        )
        diagnostics.setdefault("intent", intent)
        diagnostics.setdefault("raw_intent", intent)
        diagnostics.setdefault("requested_fact_type", requested_fact_type)
        diagnostics.setdefault("finalized_requested_fact_type", requested_fact_type)
        diagnostics.setdefault("resolution_status", "deterministic")
        diagnostics.setdefault("matched_signals", matched_signals)
        diagnostics.setdefault("subject", subject)
        diagnostics.setdefault("operational_lookup", False)
        diagnostics.update({
            "catalog_identifier": outcome.identifier,
            "catalog_query": outcome.search_query,
            "catalog_route": outcome.route,
            "catalog_match_type": outcome.match_type,
            "catalog_result_count": len(outcome.results),
            "catalog_status": outcome.status,
            "source_route": "mixed" if is_mixed_probe else "catalog",
            "catalog_probe_performed": probe is not None,
            "catalog_probe_result_count": (
                probe.result_count if probe is not None else 0
            ),
            "catalog_top_score": probe.top_score if probe is not None else None,
            "catalog_top_field_coverage": (
                probe.top_field_coverage if probe is not None else None
            ),
            "catalog_probe_coherent_results": (
                probe.coherent_result_count if probe is not None else 0
            ),
            "catalog_subject": (
                probe.catalog_subject if probe is not None else None
            ),
            "catalog_entity_terms": (
                list(probe.catalog_entity_terms) if probe is not None else []
            ),
            "catalog_assembly_context": (
                probe.catalog_assembly_context if probe is not None else None
            ),
            "corporate_evidence_available": corporate_evidence_available,
            "catalog_requirement_status": (
                "found" if outcome.results or outcome.assemblies else "not_found"
            ),
            "corporate_requirement_status": corporate_requirement_status,
            "resolved_requirements": _resolved_source_requirements(
                probe,
                corporate_requirement_status=corporate_requirement_status,
            ),
            "answer_mode": answer_mode,
        })
        return RagAnswer(
            question=question,
            answer=answer,
            model="catalog-store",
            sources=[],
            chunks_used=0,
            prompt_length=0,
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
            context_limit=self.context_limit,
            context_score_ratio=self.context_score_ratio,
            diagnostic_candidates=[],
            response_kind=response_kind,
            query_plan=diagnostics,
            resolved_question=question,
            retrieval=(
                {
                    "retrieval_stages": [
                        outcome.route,
                        *(
                            (
                                corporate_diagnostics.get("retrieval") or {}
                            ).get("retrieval_stages", [])
                            if isinstance(corporate_diagnostics, dict)
                            else []
                        ),
                    ],
                    "corporate": corporate_diagnostics,
                }
                if corporate_diagnostics is not None
                else {"retrieval_stages": [outcome.route]}
            ),
            context=(
                corporate_diagnostics.get("context")
                if isinstance(corporate_diagnostics, dict)
                else None
            ),
            evidence=(
                corporate_diagnostics.get("evidence")
                if isinstance(corporate_diagnostics, dict)
                else None
            ),
            answer_contract=(
                corporate_diagnostics.get("answer_contract")
                if isinstance(corporate_diagnostics, dict)
                else None
            ),
            contract_validation=(
                corporate_diagnostics.get("contract_validation")
                if isinstance(corporate_diagnostics, dict)
                else None
            ),
            final_answer_sections=[response_kind],
            catalog=outcome.to_dict(),
            catalog_sources=outcome.catalog_sources,
            catalog_navigation=outcome.navigation,
        )

    def _record_completed_answer(
        self,
        result: RagAnswer,
        *,
        session_id: str | None,
        user_id: str | None,
    ) -> RagAnswer:
        logger = self.interaction_logger
        if logger is None:
            return result
        try:
            interaction_id = logger.record_answer(
                result,
                session_id=session_id,
                user_id=user_id,
            )
        except Exception as exc:  # analytics must never alter answer semantics
            LOGGER.warning(
                "Interaction analytics side effect failed: %s",
                type(exc).__name__,
            )
            return replace(
                result,
                analytics_logged=False,
                analytics_error=type(exc).__name__,
            )
        return replace(
            result,
            interaction_id=interaction_id,
            analytics_logged=interaction_id is not None,
            analytics_error=(None if interaction_id is not None else logger.last_error),
        )

    def _retrieve_comparison_candidates(
        self,
        question: str,
        *,
        candidate_limit: int,
    ) -> list[RetrievedChunk]:
        chunks: list[RetrievedChunk] = []
        for topic_query in _comparison_topic_queries(question):
            chunks.extend(
                self.retriever.retrieve(
                    topic_query,
                    limit=max(self.context_limit, 3),
                    candidate_limit=candidate_limit,
                )
            )
        return chunks

    def _analyze_query(self, question: str) -> QueryAnalysisResult:
        if self.query_analyzer is None:
            try:
                from linehelper.rag.query_analyzer import QueryAnalyzer

                self.query_analyzer = QueryAnalyzer()
            except Exception as exc:
                return QueryAnalysisResult(
                    plan=None,
                    diagnostics=_query_plan_error_diagnostics(exc),
                )

        try:
            query_plan = self.query_analyzer.analyze(question)
            from linehelper.rag.query_analyzer import validate_query_plan

            query_plan = validate_query_plan(query_plan, question)
        except Exception as exc:
            return QueryAnalysisResult(
                plan=None,
                diagnostics=_query_plan_error_diagnostics(exc),
            )

        diagnostics = _query_plan_diagnostics(query_plan)
        analyzer_error = getattr(self.query_analyzer, "last_error", None)
        if analyzer_error:
            diagnostics["fallback_used"] = True
            diagnostics["fallback_reason"] = "query_analyzer_error"
            diagnostics["error"] = analyzer_error
        if not _query_plan_is_usable(query_plan):
            diagnostics["fallback_used"] = True
            diagnostics["fallback_reason"] = "empty_or_unknown_query_plan"
            return QueryAnalysisResult(plan=None, diagnostics=diagnostics)

        return QueryAnalysisResult(plan=query_plan, diagnostics=diagnostics)

    def _retrieve_with_query_plan(
        self,
        question: str,
        *,
        original_question: str,
        query_plan: QueryPlan | None,
        retrieval_limit: int,
        candidate_limit: int,
    ) -> RetrievalExecution:
        retrieval_plan = RetrievalPlanner().build(
            original_question=original_question,
            resolved_question=question,
            query_plan=query_plan,
            retrieval_limit=retrieval_limit,
            candidate_limit=candidate_limit,
        )
        retrieve_plan = getattr(self.retriever, "retrieve_plan", None)
        supports_retrieval_plan = bool(
            getattr(self.retriever, "supports_retrieval_plan", True)
        )
        if callable(retrieve_plan) and supports_retrieval_plan:
            result = retrieve_plan(
                retrieval_plan,
                candidate_limit=candidate_limit,
            )
            return RetrievalExecution(
                chunks=result.chunks,
                diagnostics=result.to_dict(),
                native=True,
            )

        if query_plan is None:
            return RetrievalExecution(
                chunks=self.retriever.retrieve(
                    question,
                    limit=retrieval_limit,
                    candidate_limit=candidate_limit,
                ),
            )

        chunks: list[RetrievedChunk] = []
        for retrieval_query in _query_plan_retrieval_queries(question, query_plan):
            chunks.extend(
                self.retriever.retrieve(
                    retrieval_query,
                    limit=retrieval_limit,
                    candidate_limit=candidate_limit,
                )
            )

        return RetrievalExecution(
            chunks=sorted(
                _boost_preferred_source_chunks(
                    _dedupe_chunks(chunks),
                    query_plan.preferred_sources,
                ),
                key=_chunk_score,
                reverse=True,
            ),
        )

def _query_plan_diagnostics(query_plan: QueryPlan) -> dict[str, Any]:
    raw_clarification = query_plan.raw_clarification
    validated_clarification = query_plan.clarification
    fact_type_resolution = query_plan.fact_type_resolution.to_dict()
    return {
        "enabled": True,
        "intent": query_plan.intent,
        "raw_intent": query_plan.raw_intent,
        "answer_type": query_plan.answer_type,
        "requested_fact_type": query_plan.requested_fact_type,
        "raw_requested_fact_type": query_plan.raw_requested_fact_type,
        "initial_requested_fact_type": fact_type_resolution[
            "initial_fact_type"
        ],
        "finalized_requested_fact_type": fact_type_resolution[
            "resolved_fact_type"
        ],
        "fact_type_resolution": fact_type_resolution,
        "resolution_status": fact_type_resolution["resolution_status"],
        "matched_signals": list(fact_type_resolution["matched_signals"]),
        "rejected_fact_types": list(
            fact_type_resolution["rejected_candidates"]
        ),
        "decision_reasons": list(
            fact_type_resolution["decision_reasons"]
        ),
        "temporal_scope": query_plan.temporal_scope,
        "raw_temporal_scope": query_plan.raw_temporal_scope,
        "subject": query_plan.subject,
        "raw_subject": query_plan.raw_subject,
        "operational_lookup": query_plan.operational_lookup,
        "operational_decision_reason": query_plan.operational_decision_reason,
        "query_plan_validation_reasons": list(query_plan.validation_reasons),
        "needs_clarification": query_plan.needs_clarification,
        "clarification_question": query_plan.clarification_question,
        "raw_clarification_required": (
            raw_clarification.required if raw_clarification is not None else False
        ),
        "validated_clarification_required": validated_clarification.required,
        "raw_clarification_kind": (
            raw_clarification.kind if raw_clarification is not None else "none"
        ),
        "validated_clarification_kind": validated_clarification.kind,
        "raw_ambiguity_span": (
            raw_clarification.ambiguity_span
            if raw_clarification is not None
            else None
        ),
        "validated_ambiguity_span": validated_clarification.ambiguity_span,
        "raw_candidate_meanings": (
            list(raw_clarification.candidate_meanings)
            if raw_clarification is not None
            else []
        ),
        "validated_candidate_meanings": list(
            validated_clarification.candidate_meanings
        ),
        "raw_missing_slots": (
            list(raw_clarification.missing_slots)
            if raw_clarification is not None
            else []
        ),
        "validated_missing_slots": list(validated_clarification.missing_slots),
        "raw_clarification_question": (
            raw_clarification.question if raw_clarification is not None else None
        ),
        "validated_clarification_question": validated_clarification.question,
        "clarification_action": query_plan.clarification_action,
        "clarification_validation_reasons": list(
            query_plan.clarification_validation_reasons
        ),
        "normalized_question": query_plan.normalized_question,
        "query_expansions": list(query_plan.query_expansions),
        "preferred_sources": list(query_plan.preferred_sources),
        "confidence": query_plan.confidence,
    }


def _query_plan_error_diagnostics(exc: Exception) -> dict[str, Any]:
    return {
        "enabled": True,
        "error": f"{type(exc).__name__}: {exc}",
        "fallback_used": True,
    }


def _pending_clarification_diagnostics(
    pending: PendingClarification,
) -> dict[str, Any]:
    """Expose an existing validated clarification without re-running analysis."""
    plan = pending.plan
    return {
        "enabled": True,
        "intent": "unknown",
        "raw_intent": None,
        "answer_type": "clarification",
        "requested_fact_type": "unknown",
        "raw_requested_fact_type": None,
        "initial_requested_fact_type": "unknown",
        "finalized_requested_fact_type": "unknown",
        "fact_type_resolution": {
            "initial_fact_type": "unknown",
            "resolved_fact_type": "unknown",
            "resolution_status": "insufficient_signals",
            "matched_signals": [],
            "rejected_candidates": [],
            "decision_reasons": [
                "pending_clarification_not_resolved"
            ],
        },
        "resolution_status": "insufficient_signals",
        "matched_signals": [],
        "rejected_fact_types": [],
        "decision_reasons": [
            "pending_clarification_not_resolved"
        ],
        "temporal_scope": "unknown",
        "raw_temporal_scope": None,
        "subject": "",
        "raw_subject": None,
        "operational_lookup": False,
        "operational_decision_reason": "pending_clarification_not_resolved",
        "query_plan_validation_reasons": [],
        "needs_clarification": True,
        "clarification_question": plan.question,
        "raw_clarification_required": False,
        "validated_clarification_required": True,
        "raw_clarification_kind": "none",
        "validated_clarification_kind": plan.kind,
        "raw_ambiguity_span": None,
        "validated_ambiguity_span": plan.ambiguity_span,
        "raw_candidate_meanings": [],
        "validated_candidate_meanings": list(plan.candidate_meanings),
        "raw_missing_slots": [],
        "validated_missing_slots": list(plan.missing_slots),
        "raw_clarification_question": None,
        "validated_clarification_question": plan.question,
        "clarification_action": "clarify",
        "clarification_validation_reasons": [
            "pending_clarification_answer_not_resolved"
        ],
        "normalized_question": pending.source_question,
        "query_expansions": [],
        "preferred_sources": [],
        "confidence": plan.confidence,
    }


def _query_plan_is_usable(query_plan: QueryPlan) -> bool:
    if query_plan.clarification_action == "clarify":
        return True
    if (
        query_plan.intent == "unknown"
        and query_plan.requested_fact_type == "unknown"
        and not query_plan.subject.strip()
    ):
        return False
    return bool(
        query_plan.requested_fact_type != "unknown"
        or query_plan.subject.strip()
        or query_plan.normalized_question.strip()
        or query_plan.query_expansions
        or query_plan.preferred_sources
    )


def _query_plan_clarification(query_plan: QueryPlan | None) -> str | None:
    if query_plan is None or query_plan.clarification_action != "clarify":
        return None
    return query_plan.clarification.question


def _runtime_query_intent(question: str, query_plan: QueryPlan | None) -> QueryIntent:
    old_intent = detect_query_intent(question)
    if query_plan is None:
        return old_intent

    if query_plan.operational_lookup:
        return QueryIntent(name="one_c_operational_lookup", require_preferred_context=True)

    if query_plan.intent == "company_ckp":
        return _intent("ckp", require_preferred_context=True)

    if query_plan.intent in {"kp_commercial_offer", "comparison"}:
        return QueryIntent(name=query_plan.intent)

    if query_plan.intent in {
        "off_topic",
        "one_c_operational_lookup",
        "equipment_it_request",
        "document_loss",
        "attendance_absence",
    }:
        return QueryIntent(name=query_plan.intent, require_preferred_context=True)

    if query_plan.intent in {
        "company_identity",
        "document_flow",
        "org_structure",
        "roles_responsibility",
        "zrs_definition",
        "zrs_approval",
        "contract_approval",
        "business_trip",
        "order_disposition",
        "task_management",
        "weekly_planning",
    }:
        anchor_terms = old_intent.anchor_terms
        return QueryIntent(
            name=query_plan.intent,
            anchor_terms=anchor_terms,
            preferred_terms=old_intent.preferred_terms,
            require_preferred_context=old_intent.require_preferred_context,
            min_context_score=old_intent.min_context_score,
        )

    return old_intent


def _query_plan_retrieval_queries(question: str, query_plan: QueryPlan) -> list[str]:
    unit_identifiers = _unit_identifiers(question)
    high_signal_identifiers = _high_signal_identifiers(question)
    queries = [
        question,
        *high_signal_identifiers,
        query_plan.normalized_question,
        *query_plan.query_expansions,
    ]
    result: list[str] = []
    seen: set[str] = set()
    for query in queries:
        clean_query = query.strip()
        if not clean_query:
            continue
        key = _normalize_for_match(clean_query)
        if key in seen:
            continue
        result.append(clean_query)
        seen.add(key)
        if unit_identifiers and not _contains_any_unit_identifier(clean_query, unit_identifiers):
            identifier_query = f"{clean_query} {' '.join(unit_identifiers)}"
            identifier_key = _normalize_for_match(identifier_query)
            if identifier_key not in seen:
                result.append(identifier_query)
                seen.add(identifier_key)
        for identifier in high_signal_identifiers:
            if _normalize_for_match(identifier) in key:
                continue
            identifier_query = f"{clean_query} {identifier}"
            identifier_key = _normalize_for_match(identifier_query)
            if identifier_key not in seen:
                result.append(identifier_query)
                seen.add(identifier_key)
    return result


def _boost_preferred_source_chunks(
    chunks: Sequence[RetrievedChunk],
    preferred_sources: Sequence[str],
) -> list[RetrievedChunk]:
    if not preferred_sources:
        return list(chunks)

    boosted_chunks: list[RetrievedChunk] = []
    normalized_sources = tuple(
        _normalize_for_match(source)
        for source in preferred_sources
        if source.strip()
    )
    for chunk in chunks:
        if _chunk_matches_preferred_source(chunk, normalized_sources):
            score = _chunk_score(chunk)
            boosted_chunks.append(
                replace(
                    chunk,
                    final_score=score + 20.0,
                    selection_reasons=[
                        *(chunk.selection_reasons or []),
                        "query analyzer preferred source boost",
                    ],
                )
            )
        else:
            boosted_chunks.append(chunk)
    return boosted_chunks


def _chunk_matches_preferred_source(
    chunk: RetrievedChunk,
    normalized_sources: Sequence[str],
) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                metadata.get("source_file"),
                metadata.get("logical_unit_title"),
            )
        )
    )
    return any(source in haystack for source in normalized_sources)


def detect_query_intent(question: str) -> QueryIntent:
    """Detect a simple transparent query intent without external models."""
    normalized = _normalize_for_match(question)

    if _is_ckp_question(normalized):
        return _intent("ckp", require_preferred_context=True)

    if _is_kp_commercial_offer_question(normalized):
        return QueryIntent(name="kp_commercial_offer")

    if _is_ambiguous_kp_question(normalized):
        return QueryIntent(name="kp_ambiguous")

    if is_comparison_question(normalized):
        return _intent("comparison")

    if _contains_any(
        normalized,
        (
            "чем занимается компания",
            "что делает компания",
            "о компании",
            "цель компании",
            "serviceline",
            "сервислайн",
        ),
    ):
        return _intent("company_identity", require_preferred_context=True)

    if _contains_any(
        normalized,
        (
            "документооборот",
            "документоборот",
            "1с до",
            "1с документооборот",
            "согласование",
            "создать документ",
        ),
    ):
        return _intent("document_flow", require_preferred_context=True)

    tokens = set(_tokens(normalized))
    if "документ" in tokens or "документы" in tokens:
        return _intent("document_flow", require_preferred_context=True)

    if _contains_any(normalized, ANCHOR_TERMS["отпуск"]):
        return _intent("vacation", anchor_terms=ANCHOR_TERMS["отпуск"])

    if _contains_any(normalized, ANCHOR_TERMS["зрс"]):
        return _intent("zrs", anchor_terms=ANCHOR_TERMS["зрс"])

    return QueryIntent(name="unknown")


def should_ask_clarification(question: str) -> str | None:
    """Compatibility helper backed by the validated fallback QueryPlan."""
    from linehelper.rag.query_analyzer import fallback_query_plan

    return _query_plan_clarification(fallback_query_plan(question))


def select_context_chunks(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    intent: QueryIntent | None = None,
    max_chunks: int = DEFAULT_CONTEXT_LIMIT,
    score_ratio: float = DEFAULT_CONTEXT_SCORE_RATIO,
) -> list[RetrievedChunk]:
    """Select a compact, high-confidence context for the LLM prompt."""
    if not chunks:
        return []

    max_chunks = max(1, max_chunks)
    score_ratio = max(0.0, min(score_ratio, 1.0))
    candidates = _dedupe_context_candidates(chunks)
    intent = intent or detect_query_intent(question)

    if intent.name == "comparison":
        return _select_comparison_context(
            question,
            candidates,
            max_chunks=max_chunks,
            score_ratio=score_ratio,
        )

    preferred = _chunks_matching_terms(candidates, intent.preferred_terms)
    if preferred:
        candidates = preferred
    elif intent.require_preferred_context:
        return []

    anchor_terms = _active_anchor_terms(question, intent=intent)
    if anchor_terms:
        anchored = [
            chunk
            for chunk in candidates
            if _chunk_contains_any_term(chunk, anchor_terms)
        ]
        if anchored:
            candidates = anchored

    ranked_candidates = _sort_context_candidates(question, candidates, intent=intent)
    candidates = ranked_candidates
    top_score = max(_context_selection_score(chunk, question, intent=intent) for chunk in candidates)
    if top_score > 0 and score_ratio > 0:
        cutoff = top_score * score_ratio
        candidates = [
            chunk
            for chunk in candidates
            if _context_selection_score(chunk, question, intent=intent) >= cutoff
        ]

    candidates = _ensure_organization_context_diversity(
        question,
        candidates,
        ranked_candidates,
        intent=intent,
    )

    if intent.name == "unknown" and candidates:
        candidates = [
            chunk
            for chunk in candidates
            if _chunk_score(chunk) >= intent.min_context_score
        ]

    return candidates[:max_chunks]


def has_sufficient_context(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    intent: QueryIntent | None = None,
) -> bool:
    """Return whether selected chunks are safe to expose to the LLM as sources."""
    if not chunks:
        return False

    intent = intent or detect_query_intent(question)
    if intent.name == "comparison":
        return _has_sufficient_comparison_context(question, chunks)

    if intent.name != "unknown":
        return True

    significant_terms = _significant_question_terms(question)
    if not significant_terms:
        return False

    return any(
        _chunk_question_evidence_score(chunk, significant_terms) >= 2
        for chunk in chunks
    )


def _boost_organization_chunks(
    chunks: Sequence[RetrievedChunk],
    question: str,
    *,
    intent: QueryIntent,
) -> list[RetrievedChunk]:
    if intent.name not in ORGANIZATION_INTENTS:
        return list(chunks)

    boosted: list[RetrievedChunk] = []
    for chunk in chunks:
        boost = _organization_context_boost(chunk, question, intent=intent)
        if boost <= 0:
            boosted.append(chunk)
            continue
        boosted.append(
            replace(
                chunk,
                final_score=_chunk_score(chunk) + boost,
                selection_reasons=[
                    *(chunk.selection_reasons or []),
                    f"organization metadata boost +{boost:.0f}",
                ],
            )
        )
    return sorted(boosted, key=_chunk_score, reverse=True)


def _sort_context_candidates(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    intent: QueryIntent,
) -> list[RetrievedChunk]:
    return sorted(
        chunks,
        key=lambda chunk: _context_selection_score(chunk, question, intent=intent),
        reverse=True,
    )


def _context_selection_score(
    chunk: RetrievedChunk,
    question: str,
    *,
    intent: QueryIntent,
) -> float:
    score = _chunk_score(chunk)
    if intent.name in ORGANIZATION_INTENTS:
        score += _organization_context_boost(chunk, question, intent=intent)
    return score


def _organization_context_boost(
    chunk: RetrievedChunk,
    question: str,
    *,
    intent: QueryIntent,
) -> float:
    if intent.name not in ORGANIZATION_INTENTS:
        return 0.0

    metadata = chunk.metadata or {}
    doc_type = _organization_doc_type(chunk)
    is_structured = _is_structured_organization_chunk(chunk)
    boost = 0.0

    if is_structured:
        boost += 55.0

    if _chunk_matches_unit_identifier(chunk, _unit_identifiers(question)):
        boost += 320.0

    if _chunk_matches_high_signal_identifier(chunk, _high_signal_identifiers(question)):
        boost += 300.0

    if intent.name == "org_structure":
        if _is_org_chart_rules_question(question) and _is_org_chart_rules_chunk(chunk):
            boost += 320.0
        boost += {
            "organization_unit": 170.0,
            "organization_overview": 80.0,
            "employee_role": 35.0,
            "responsibility_route": 25.0,
            "role_combination": 20.0,
            "organization_status": 15.0,
            "organization_vacancy": 15.0,
        }.get(doc_type, 0.0)

    elif intent.name == "roles_responsibility":
        topic_matches = _organization_topic_matches(chunk, question)
        unit_matches = _chunk_matches_unit_identifier(chunk, _unit_identifiers(question))
        if _is_leadership_question(question):
            unit_head_bonus = 500.0 if metadata.get("head_name") and (topic_matches or unit_matches) else 45.0
            boost += {
                "organization_unit": unit_head_bonus,
                "employee_role": 440.0 if topic_matches or unit_matches else 35.0,
                "role_combination": 120.0 if topic_matches or unit_matches else 25.0,
                "responsibility_route": 0.0,
            }.get(doc_type, 0.0)
        elif _is_contact_route_question(question):
            route_bonus = 335.0 if topic_matches or unit_matches else 70.0
            boost += {
                "responsibility_route": route_bonus,
                "employee_role": 145.0 if topic_matches or unit_matches else 45.0,
                "organization_unit": 75.0 if topic_matches or unit_matches else 30.0,
                "role_combination": 60.0 if topic_matches or unit_matches else 20.0,
            }.get(doc_type, 0.0)
        else:
            boost += {
                "organization_unit": 130.0,
                "employee_role": 120.0,
                "responsibility_route": 80.0,
                "role_combination": 75.0,
            }.get(doc_type, 0.0)

    return boost


def _dedupe_context_candidates(chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    result: list[RetrievedChunk] = []
    seen: set[tuple[str, str, str | None]] = set()
    for chunk in chunks:
        key = (chunk.source, chunk.title, chunk.section)
        if key in seen:
            continue
        result.append(chunk)
        seen.add(key)
    return result


def _ensure_organization_context_diversity(
    question: str,
    candidates: Sequence[RetrievedChunk],
    ranked_candidates: Sequence[RetrievedChunk],
    *,
    intent: QueryIntent,
) -> list[RetrievedChunk]:
    selected = list(candidates)
    if intent.name not in ORGANIZATION_INTENTS or not _is_leadership_question(question):
        return selected

    if any(_organization_doc_type(chunk) == "employee_role" for chunk in selected):
        return selected

    for chunk in ranked_candidates:
        if _organization_doc_type(chunk) != "employee_role":
            continue
        if not (
            _organization_topic_matches(chunk, question)
            or _chunk_matches_unit_identifier(chunk, _unit_identifiers(question))
        ):
            continue
        selected.append(chunk)
        return _dedupe_context_candidates(selected)

    return selected


def _is_structured_organization_chunk(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    return (
        metadata.get("knowledge_domain") == ORGANIZATION_KNOWLEDGE_DOMAIN
        or _organization_doc_type(chunk) in ORGANIZATION_STRUCTURED_DOC_TYPES
        or ORGANIZATION_STRUCTURED_SOURCE_TITLE in chunk.source
        or metadata.get("source_file") == ORGANIZATION_STRUCTURED_SOURCE_TITLE
    )


def _organization_doc_type(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    return str(chunk.doc_type or metadata.get("doc_type") or "").strip()


def _unit_identifiers(question: str) -> tuple[str, ...]:
    identifiers = []
    seen: set[str] = set()
    for match in UNIT_IDENTIFIER_RE.findall(question):
        if not any(char.isdigit() for char in match):
            continue
        identifier = match.upper()
        key = identifier.casefold()
        if key in seen:
            continue
        identifiers.append(identifier)
        seen.add(key)
    return tuple(identifiers)


def _high_signal_identifiers(question: str) -> tuple[str, ...]:
    identifiers = []
    seen: set[str] = set()
    for match in ORGANIZATION_HIGH_SIGNAL_IDENTIFIER_RE.findall(question):
        identifier = match.upper()
        key = identifier.casefold()
        if key in seen:
            continue
        identifiers.append(identifier)
        seen.add(key)
    return tuple(identifiers)


def _contains_any_unit_identifier(value: str, identifiers: Sequence[str]) -> bool:
    haystack = _normalize_for_match(value)
    return any(_normalize_for_match(identifier) in haystack for identifier in identifiers)


def _chunk_matches_unit_identifier(
    chunk: RetrievedChunk,
    identifiers: Sequence[str],
) -> bool:
    if not identifiers:
        return False
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.section,
                metadata.get("unit_number"),
                metadata.get("unit_id"),
                metadata.get("unit_ids"),
                metadata.get("record_key"),
            )
        )
    )
    return any(_normalize_for_match(identifier) in haystack for identifier in identifiers)


def _chunk_matches_high_signal_identifier(
    chunk: RetrievedChunk,
    identifiers: Sequence[str],
) -> bool:
    if not identifiers:
        return False
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.section,
                chunk.doc_type,
                metadata.get("record_key"),
                metadata.get("employee_name"),
                metadata.get("unit_name"),
                metadata.get("topic"),
            )
        )
    )
    return any(_normalize_for_match(identifier) in haystack for identifier in identifiers)


def _is_leadership_question(question: str) -> bool:
    normalized = _normalize_for_match(question)
    return _contains_any(normalized, LEADERSHIP_TERMS)


def _is_contact_route_question(question: str) -> bool:
    normalized = _normalize_for_match(question)
    return _contains_any(normalized, CONTACT_ROUTE_TERMS)


def _organization_topic_matches(chunk: RetrievedChunk, question: str) -> bool:
    terms = _organization_topic_terms(question)
    if not terms:
        return False
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.section,
                chunk.doc_type,
                metadata.get("source_file"),
                metadata.get("record_key"),
                metadata.get("employee_name"),
                metadata.get("unit_name"),
                metadata.get("topic"),
                chunk.text,
            )
        )
    )
    return any(term in haystack for term in terms)


def _organization_topic_terms(question: str) -> tuple[str, ...]:
    normalized = _normalize_for_match(question)
    known_terms = (
        "закуп",
        "логист",
        "тамож",
        "склад",
        "достав",
        "отгруз",
        "krones",
        "khs",
        "heuft",
        "sidel",
        "smi",
        "imeta",
        "кадр",
        "проект",
        "модел",
        "эфес",
        "efes",
    )
    return tuple(term for term in known_terms if term in normalized)


def _is_org_chart_rules_question(question: str) -> bool:
    normalized = _normalize_for_match(question)
    return "оргсхем" in normalized and _contains_any(
        normalized,
        ("что такое", "для чего", "зачем", "правила", "регламент"),
    )


def _is_org_chart_rules_chunk(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                chunk.section,
                metadata.get("source_file"),
            )
        )
    )
    return "регламент" in haystack and "оргсхем" in haystack


def is_comparison_question(question: str) -> bool:
    """Return whether the question compares two meanings or processes."""
    normalized = _normalize_for_match(question)
    if _contains_any(normalized, COMPARISON_TRIGGERS):
        return True
    return len(_active_topic_groups(normalized)) >= 2


def strip_trailing_source_block(answer: str) -> str:
    """Remove a trailing source list if the LLM ignored the prompt contract."""
    return _TRAILING_SOURCE_BLOCK_RE.sub("", answer.strip()).rstrip()


def _intent(
    name: str,
    *,
    anchor_terms: tuple[str, ...] = (),
    require_preferred_context: bool = False,
) -> QueryIntent:
    return QueryIntent(
        name=name,
        anchor_terms=anchor_terms or INTENT_ANCHOR_TERMS.get(name, ()),
        preferred_terms=INTENT_PREFERRED_TERMS.get(name, ()),
        require_preferred_context=require_preferred_context,
    )


def _select_comparison_context(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    max_chunks: int,
    score_ratio: float,
) -> list[RetrievedChunk]:
    topic_groups = _active_topic_groups(question)
    if not topic_groups:
        return chunks[:max_chunks]

    selected: list[RetrievedChunk] = []
    for _, terms in topic_groups:
        group_chunks = [
            chunk for chunk in chunks if _chunk_contains_any_term(chunk, terms)
        ]
        if not group_chunks:
            continue
        selected.append(_best_group_chunk(group_chunks, terms))

    selected = _dedupe_chunks(selected)
    if len(selected) >= max_chunks:
        return selected[:max_chunks]

    top_score = max((_chunk_score(chunk) for chunk in chunks), default=0.0)
    cutoff = top_score * score_ratio if top_score > 0 and score_ratio > 0 else 0.0
    topic_terms = tuple(term for _, terms in topic_groups for term in terms)

    for chunk in chunks:
        if chunk in selected:
            continue
        if _chunk_score(chunk) < cutoff and not _chunk_contains_any_term(chunk, topic_terms):
            continue
        selected.append(chunk)
        if len(selected) >= max_chunks:
            break

    return selected


def _best_group_chunk(
    chunks: Sequence[RetrievedChunk],
    terms: Sequence[str],
) -> RetrievedChunk:
    return max(
        chunks,
        key=lambda chunk: (
            _strong_chunk_contains_any_term(chunk, terms),
            _chunk_score(chunk),
        ),
    )


def _dedupe_chunks(chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    result: list[RetrievedChunk] = []
    seen: set[tuple[int | None, str, str | None, int | None]] = set()
    for chunk in chunks:
        key = (
            chunk.chunk_id,
            chunk.title,
            chunk.section,
            chunk.page,
        )
        if key in seen:
            continue
        result.append(chunk)
        seen.add(key)
    return result


def _comparison_topic_queries(question: str) -> tuple[str, ...]:
    query_by_group = {
        "отпуск": "отпуск оформление отпуск",
        "задачи": "взять задачу в работу Работа с задачами",
        "контроль": "контроль распоряжение письменная форма контроль",
        "зрс": "ЗРС завершенная работа сотрудника",
        "цкп": "ЦКП ценный конечный продукт",
        "командировка": "согласование командировки",
        "договор": "согласование договора документооборот",
        "распоряжение": "ИП-0005 Распоряжения контроль",
    }
    queries = [
        query_by_group.get(name, " ".join(terms[:3]))
        for name, terms in _active_topic_groups(question)
    ]
    return tuple(dict.fromkeys(query for query in queries if query.strip()))


def _is_ambiguous_kp_question(question: str) -> bool:
    normalized = _normalize_for_match(question)
    return (
        _KP_RE.search(normalized) is not None
        and not _is_kp_commercial_offer_question(normalized)
        and not _is_ckp_question(normalized)
    )


def _is_kp_commercial_offer_question(question: str) -> bool:
    normalized = _normalize_for_match(question)
    return _KP_RE.search(normalized) is not None and _contains_any(
        normalized,
        (
            "коммерческое предложение",
            "коммерческого предложения",
            "коммерческому предложению",
            "коммерческим предложением",
            "коммерческих предложений",
        ),
    )


def _is_ckp_question(question: str) -> bool:
    normalized = _normalize_for_match(question)
    return "цкп" in normalized or _contains_any(
        normalized,
        (
            "ценный конечный продукт",
            "ценного конечного продукта",
            "ценному конечному продукту",
            "ценным конечным продуктом",
        ),
    )


def _source_from_chunk(chunk: RetrievedChunk) -> RagSource:
    metadata = chunk.metadata or {}
    logical_unit_title = metadata.get("logical_unit_title")

    return RagSource(
        title=chunk.title,
        source=chunk.source,
        section=chunk.section,
        page=chunk.page,
        logical_unit_title=str(logical_unit_title) if logical_unit_title else None,
        score=chunk.final_score if chunk.final_score is not None else chunk.score,
        matched_excerpt=chunk.matched_excerpt,
    )


def _active_anchor_terms(question: str, *, intent: QueryIntent | None = None) -> tuple[str, ...]:
    normalized_question = _normalize_for_match(question)
    terms: list[str] = []

    for variants in ANCHOR_TERMS.values():
        if any(variant in normalized_question for variant in variants):
            terms.extend(variants)

    if intent is not None:
        terms.extend(intent.anchor_terms)

    return tuple(dict.fromkeys(terms))


def _active_topic_groups(question: str) -> list[tuple[str, tuple[str, ...]]]:
    normalized_question = _normalize_for_match(question)
    groups: list[tuple[str, tuple[str, ...]]] = []

    for name, variants in ANCHOR_TERMS.items():
        if any(variant in normalized_question for variant in variants):
            groups.append((name, variants))

    return groups


def _has_sufficient_comparison_context(
    question: str,
    chunks: Sequence[RetrievedChunk],
) -> bool:
    topic_groups = _active_topic_groups(question)
    if len(topic_groups) < 2:
        return bool(chunks)

    covered_groups = {
        name
        for name, terms in topic_groups
        if any(_chunk_contains_any_term(chunk, terms) for chunk in chunks)
    }
    return len(covered_groups) >= 2


def _chunk_contains_any_term(chunk: RetrievedChunk, terms: Sequence[str]) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                chunk.section,
                metadata.get("source_file"),
                metadata.get("logical_unit_title"),
                metadata.get("doc_type"),
                " ".join(str(tag) for tag in metadata.get("tags", [])),
                chunk.text,
            )
        )
    )
    return any(term in haystack for term in terms)


def _strong_chunk_contains_any_term(chunk: RetrievedChunk, terms: Sequence[str]) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                chunk.section,
                metadata.get("source_file"),
                metadata.get("logical_unit_title"),
                metadata.get("doc_type"),
                " ".join(str(tag) for tag in metadata.get("tags", [])),
            )
        )
    )
    return any(term in haystack for term in terms)


def _chunk_score(chunk: RetrievedChunk) -> float:
    score = chunk.final_score if chunk.final_score is not None else chunk.score
    if score is None:
        return 0.0
    return float(score)


def _chunks_matching_terms(
    chunks: Sequence[RetrievedChunk],
    terms: Sequence[str],
) -> list[RetrievedChunk]:
    if not terms:
        return []
    return [
        chunk
        for chunk in chunks
        if _chunk_contains_any_term(chunk, terms)
    ]


def _no_answer_message(intent: QueryIntent) -> str:
    return NO_ANSWER_MESSAGE


def _contains_any(value: str, needles: Sequence[str]) -> bool:
    return any(_normalize_for_match(needle) in value for needle in needles)


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_RE.findall(value)]


def _significant_question_terms(question: str) -> tuple[str, ...]:
    terms = []
    for token in _tokens(_normalize_for_match(question)):
        if token in _GENERIC_CONTEXT_STOP_WORDS:
            continue
        if len(token) < 4 and not any(char.isdigit() for char in token):
            continue
        terms.append(token)
    return tuple(dict.fromkeys(terms))


def _chunk_question_evidence_score(
    chunk: RetrievedChunk,
    terms: Sequence[str],
) -> int:
    metadata = chunk.metadata or {}
    strong_haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.source,
                chunk.section,
                metadata.get("source_file"),
                metadata.get("logical_unit_title"),
                metadata.get("doc_type"),
                " ".join(str(tag) for tag in metadata.get("tags", [])),
            )
        )
    )
    weak_haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.matched_excerpt,
                chunk.text,
            )
        )
    )

    score = 0
    for term in terms:
        if _term_matches_haystack(term, strong_haystack):
            score += 2
        elif _term_matches_haystack(term, weak_haystack):
            score += 1
    return score


def _term_matches_haystack(term: str, haystack: str) -> bool:
    if term in haystack:
        return True

    if len(term) <= 5:
        return False

    prefix = _term_prefix(term)
    return len(prefix) >= 5 and prefix in haystack


def _term_prefix(term: str) -> str:
    for suffix in (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ому",
        "ыми",
        "ими",
        "ий",
        "ый",
        "ой",
        "ая",
        "яя",
        "ое",
        "ее",
        "ии",
        "ия",
        "ие",
        "ых",
        "их",
        "ую",
        "юю",
        "ам",
        "ям",
        "ах",
        "ях",
        "ов",
        "ев",
        "ей",
        "ом",
        "ем",
        "а",
        "я",
        "ы",
        "и",
        "е",
        "у",
        "ю",
    ):
        if term.endswith(suffix) and len(term) - len(suffix) >= 5:
            return term[: -len(suffix)]
    return term


def _normalize_for_match(value: str) -> str:
    return value.lower().replace("ё", "е")


def _catalog_requirement_subject(probe_query: str, analyzed_subject: str) -> str:
    """Use QueryPlan normalization only when it adds no unprobed concepts."""
    probe_terms = tuple(_term_prefix(term) for term in _tokens(probe_query))
    analyzed_terms = tuple(_term_prefix(term) for term in _tokens(analyzed_subject))
    if len(probe_terms) != len(analyzed_terms):
        return probe_query
    equivalent = all(
        probe.startswith(analyzed) or analyzed.startswith(probe)
        for probe, analyzed in zip(probe_terms, analyzed_terms, strict=True)
    )
    return analyzed_subject if equivalent else probe_query


def _mixed_catalog_domain_consistency(
    probe: CatalogProbeDiagnostics | None,
    chunks: Sequence[RetrievedChunk],
) -> dict[str, Any]:
    """Require positive equipment-subject anchors for mixed corporate evidence."""
    if probe is None or probe.source_route != "mixed" or probe.outcome is None:
        return {
            "checked": False,
            "passed": True,
            "reason": "not_a_resolved_mixed_catalog_requirement",
            "rejected_chunk_ids": (),
            "accepted_chunks": tuple(chunks),
        }
    subject = probe.catalog_subject or probe.query
    anchors = tuple(
        dict.fromkeys(
            prefix
            for token in _tokens(subject)
            if len(prefix := _term_prefix(token)) >= 5
            and prefix not in {
                "верхн",
                "нижн",
                "част",
                "узел",
                "детал",
                "устройств",
            }
        )
    )
    if not chunks:
        return {
            "checked": True,
            "passed": False,
            "reason": "no_corporate_supporting_evidence",
            "rejected_chunk_ids": (),
            "accepted_chunks": (),
        }
    if not anchors:
        return {
            "checked": True,
            "passed": False,
            "reason": "catalog_subject_has_no_specific_domain_anchor",
            "rejected_chunk_ids": tuple(_chunk_diagnostic_id(chunk) for chunk in chunks),
            "accepted_chunks": (),
        }
    matched = tuple(
        chunk
        for chunk in chunks
        if any(_chunk_has_domain_anchor(chunk, anchor) for anchor in anchors)
    )
    if matched:
        return {
            "checked": True,
            "passed": True,
            "reason": "corporate_evidence_matches_catalog_subject",
            "rejected_chunk_ids": tuple(
                _chunk_diagnostic_id(chunk)
                for chunk in chunks
                if chunk not in matched
            ),
            "accepted_chunks": matched,
        }
    return {
        "checked": True,
        "passed": False,
        "reason": "corporate_evidence_lacks_catalog_subject_anchor",
        "rejected_chunk_ids": tuple(_chunk_diagnostic_id(chunk) for chunk in chunks),
        "accepted_chunks": (),
    }


def _chunk_has_domain_anchor(chunk: RetrievedChunk, anchor: str) -> bool:
    metadata = chunk.metadata or {}
    haystack = _normalize_for_match(
        " ".join(
            str(value or "")
            for value in (
                chunk.title,
                chunk.section,
                metadata.get("logical_unit_title"),
                chunk.matched_excerpt,
                chunk.text,
            )
        )
    )
    tokens = {
        _normalize_for_match(token)
        for token in _tokens(haystack)
        if len(token) >= 5
    }
    return any(
        token.startswith(anchor) and len(token) - len(anchor) <= 5
        for token in tokens
    )


def _chunk_diagnostic_id(chunk: RetrievedChunk) -> object:
    return chunk.chunk_id


def _with_catalog_probe_diagnostics(
    diagnostics: dict[str, Any] | None,
    probe: CatalogProbeDiagnostics | None,
    *,
    corporate_evidence_available: bool,
    source_route: str | None = None,
    corporate_requirement_status: str | None = None,
    answer_mode: str | None = None,
) -> dict[str, Any]:
    result = dict(diagnostics or {})
    if probe is None:
        return result
    outcome = probe.outcome
    resolved_source_route = source_route or probe.source_route
    resolved_corporate_status = corporate_requirement_status or (
        "found"
        if corporate_evidence_available
        else "pending"
        if resolved_source_route == "mixed"
        else "not_requested"
    )
    has_catalog_requirement = any(
        requirement.source == "catalog"
        for requirement in probe.resolved_requirements
    )
    catalog_status = (
        "found"
        if isinstance(outcome, CatalogChatOutcome) and outcome.results
        else "not_found"
        if has_catalog_requirement
        else "not_requested"
    )
    result.update(
        {
            "source_route": resolved_source_route,
            "catalog_probe_performed": probe.performed,
            "catalog_probe_result_count": probe.result_count,
            "catalog_result_count": probe.result_count,
            "catalog_match_type": (
                outcome.match_type if isinstance(outcome, CatalogChatOutcome) else None
            ),
            "catalog_top_score": probe.top_score,
            "catalog_top_field_coverage": probe.top_field_coverage,
            "catalog_probe_coherent_results": probe.coherent_result_count,
            "catalog_subject": probe.catalog_subject,
            "catalog_entity_terms": list(probe.catalog_entity_terms),
            "catalog_assembly_context": probe.catalog_assembly_context,
            "corporate_evidence_available": corporate_evidence_available,
            "catalog_requirement_status": catalog_status,
            "corporate_requirement_status": resolved_corporate_status,
            "resolved_requirements": _resolved_source_requirements(
                probe,
                corporate_requirement_status=resolved_corporate_status,
            ),
            "answer_mode": answer_mode or (
                "full_mixed"
                if resolved_source_route == "mixed"
                and corporate_evidence_available
                and catalog_status == "found"
                else "partial_mixed"
                if resolved_source_route == "mixed"
                and (corporate_evidence_available or catalog_status == "found")
                else resolved_source_route
            ),
        }
    )
    return result


def _resolved_source_requirements(
    probe: CatalogProbeDiagnostics | None,
    *,
    corporate_requirement_status: str,
) -> list[dict[str, object]]:
    if probe is None:
        return []
    resolved = []
    for requirement in probe.resolved_requirements:
        item = requirement.to_dict()
        if requirement.source == "corporate":
            item["status"] = corporate_requirement_status
        resolved.append(item)
    return resolved


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
