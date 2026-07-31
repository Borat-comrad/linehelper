"""LLM query analyzer that returns a structured search plan."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Protocol

from linehelper.llm.ollama_client import OllamaClient
from linehelper.rag.requested_fact_type_resolver import (
    ALLOWED_REQUESTED_FACT_TYPES,
    RequestedFactTypeResolution,
    RequestedFactTypeResolver,
)


DEFAULT_ANALYZER_MODEL = "qwen2.5:3b"
ANALYZER_NUM_PREDICT = 900
ORGANIZATION_STRUCTURED_SOURCE_TITLE = "bvr_company_structure_instruction_v2 (2).txt"
ORGANIZATION_LEGACY_SOURCE_TITLE = "2026-03-03_Оргсхема _ Компании"

ALLOWED_INTENTS = frozenset(
    {
        "company_identity",
        "company_ckp",
        "org_structure",
        "roles_responsibility",
        "zrs_definition",
        "zrs_approval",
        "vacation",
        "document_flow",
        "contract_approval",
        "business_trip",
        "order_disposition",
        "task_management",
        "weekly_planning",
        "statistics_kpi",
        "onboarding_position",
        "written_communication",
        "equipment_it_request",
        "kp_commercial_offer",
        "ambiguous_abbreviation",
        "document_loss",
        "attendance_absence",
        "one_c_operational_lookup",
        "off_topic",
        "unknown",
    }
)

ALLOWED_ANSWER_TYPES = frozenset(
    {
        "definition",
        "procedure",
        "list",
        "comparison",
        "clarification",
        "partial_answer",
        "no_answer",
        "general",
    }
)

ALLOWED_TEMPORAL_SCOPES = frozenset(
    {
        "static",
        "current",
        "historical",
        "unknown",
    }
)

ALLOWED_CLARIFICATION_KINDS = frozenset(
    {
        "none",
        "abbreviation",
        "lexical_ambiguity",
        "missing_subject",
        "missing_object",
        "missing_document_type",
        "missing_scope",
        "missing_required_slot",
    }
)

ALLOWED_CLARIFICATION_ACTIONS = frozenset(
    {
        "clarify",
        "continue_retrieval",
    }
)

AMBIGUITY_REGISTRY: dict[str, tuple[str, ...]] = {
    "КП": (
        "коммерческое предложение",
        "ценный конечный продукт",
    ),
}

STATIC_FACT_TYPES = frozenset(
    {
        "definition",
        "procedure",
        "responsible_person",
        "primary_contact",
        "unit_head",
        "document_recipient",
        "list",
        "comparison",
    }
)

CURRENT_OPERATIONAL_FACT_TYPES = frozenset(
    {
        "current_status",
        "current_value",
        "price",
        "availability",
    }
)

KNOWN_SOURCE_TITLES = frozenset(
    {
        ORGANIZATION_LEGACY_SOURCE_TITLE,
        ORGANIZATION_STRUCTURED_SOURCE_TITLE,
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
        "ИП-0004 Структура ЗРС",
        "ИП-0005 Распоряжения",
        "ИП-0006 Документооборот",
        "Инструкция Согласования договоров в Документообороте",
        "Инструкция Согласования командировки в Документообороте",
        "Инструкция Согласования ЗРС в Документообороте",
        "Регламент по письменной коммуникации",
        "Регламент по планированию на неделю",
    }
)

INTENT_SOURCE_COMPATIBILITY: dict[str, set[str]] = {
    "company_identity": {
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
    },
    "company_ckp": {"ИП-0003 ЦКП SERVICELINE"},
    "org_structure": {
        ORGANIZATION_LEGACY_SOURCE_TITLE,
        ORGANIZATION_STRUCTURED_SOURCE_TITLE,
    },
    "roles_responsibility": {
        ORGANIZATION_LEGACY_SOURCE_TITLE,
        ORGANIZATION_STRUCTURED_SOURCE_TITLE,
    },
    "zrs_definition": {"ИП-0004 Структура ЗРС"},
    "zrs_approval": {
        "ИП-0004 Структура ЗРС",
        "Инструкция Согласования ЗРС в Документообороте",
    },
    "vacation": set(),
    "document_flow": {"ИП-0006 Документооборот"},
    "contract_approval": {
        "Инструкция Согласования договоров в Документообороте",
        "ИП-0006 Документооборот",
    },
    "business_trip": {
        "Инструкция Согласования командировки в Документообороте",
        "ИП-0006 Документооборот",
    },
    "order_disposition": {"ИП-0005 Распоряжения"},
    "task_management": {"ИП-0005 Распоряжения"},
    "weekly_planning": {"Регламент по планированию на неделю"},
    "statistics_kpi": set(),
    "onboarding_position": set(),
    "equipment_it_request": set(),
    "document_loss": set(),
    "attendance_absence": set(),
    "one_c_operational_lookup": set(),
    "kp_commercial_offer": set(),
    "ambiguous_abbreviation": set(),
    "off_topic": set(),
    "unknown": set(),
}

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_KP_RE = re.compile(r"(?<![0-9a-zа-яё])кп(?![0-9a-zа-яё])", re.IGNORECASE)
_FORBIDDEN_CKP_MEANING_RE = re.compile(
    r"центр\s+комплексных\s+предложений",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClarificationPlan:
    """Analyzer proposal or validated decision about a clarification turn."""

    required: bool = False
    kind: str = "none"
    ambiguity_span: str | None = None
    candidate_meanings: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    question: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class ClarificationDecision:
    """Raw and validated clarification plans plus the runtime action."""

    raw: ClarificationPlan = field(default_factory=ClarificationPlan)
    validated: ClarificationPlan = field(default_factory=ClarificationPlan)
    action: str = "continue_retrieval"
    validation_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueryPlan:
    intent: str
    normalized_question: str
    query_expansions: list[str]
    preferred_sources: list[str]
    answer_type: str
    needs_clarification: bool
    clarification_question: str | None
    confidence: float
    notes: str | None
    requested_fact_type: str = "unknown"
    temporal_scope: str = "unknown"
    subject: str = ""
    operational_lookup: bool = False
    operational_decision_reason: str = "not_evaluated"
    raw_intent: str | None = None
    raw_requested_fact_type: str | None = None
    raw_temporal_scope: str | None = None
    raw_subject: str | None = None
    validation_reasons: list[str] = field(default_factory=list)
    clarification: ClarificationPlan = field(default_factory=ClarificationPlan)
    raw_clarification: ClarificationPlan | None = None
    clarification_action: str = "continue_retrieval"
    clarification_validation_reasons: list[str] = field(default_factory=list)
    fact_type_resolution: RequestedFactTypeResolution = field(
        default_factory=RequestedFactTypeResolution
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict for diagnostics and smoke scripts."""
        return asdict(self)


class AnalyzerChatClient(Protocol):
    model: str

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """Return model text for analyzer messages."""


class QueryAnalyzer:
    """Build a validated QueryPlan for the runtime RAG flow."""

    def __init__(
        self,
        ollama_client: AnalyzerChatClient | None = None,
        model: str | None = None,
    ) -> None:
        self.model = model or _load_analyzer_model()
        self.ollama_client = ollama_client or OllamaClient(
            model=self.model,
            temperature=0,
            num_predict=ANALYZER_NUM_PREDICT,
        )
        self.last_error: str | None = None

    def analyze(self, question: str) -> QueryPlan:
        """Analyze a question into a QueryPlan, falling back safely on errors."""
        clean_question = question.strip()
        if not clean_question:
            self.last_error = "empty question"
            return fallback_query_plan(question)

        messages = build_query_analyzer_prompt(clean_question)
        self.last_error = None

        try:
            content = self.ollama_client.chat(
                messages,
                model=self.model,
                temperature=0,
                num_predict=ANALYZER_NUM_PREDICT,
            )
            return parse_query_plan_response(content, clean_question)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return fallback_query_plan(clean_question)


def build_query_analyzer_prompt(question: str) -> list[dict[str, str]]:
    """Build strict chat messages for the JSON-only query analyzer."""
    system_prompt = f"""
Ты LLM Query Analyzer для локального корпоративного RAG LineHelper.
Твоя задача - НЕ отвечать пользователю, а вернуть один JSON-объект QueryPlan.

Правила ответа:
- Верни только JSON.
- Не используй Markdown.
- Не добавляй пояснения до или после JSON.
- Не отвечай на вопрос пользователя.
- Не выдумывай источники; preferred_sources заполняй только если источник очевиден из корпоративной карты.
- preferred_sources может содержать только эти known source titles: {", ".join(sorted(KNOWN_SOURCE_TITLES))}.
- Источник должен быть не только известным, но и подходящим по intent.
- Если подходящего source title нет в списке known source titles, верни preferred_sources=[].
- Если точного подходящего источника нет, лучше вернуть preferred_sources=[], чем выбирать похожий источник.
- Верни ровно один JSON-объект.
- intent должен быть одним из: {", ".join(sorted(ALLOWED_INTENTS))}.
- answer_type должен быть одним из: {", ".join(sorted(ALLOWED_ANSWER_TYPES))}.
- requested_fact_type должен быть одним из: {", ".join(sorted(ALLOWED_REQUESTED_FACT_TYPES))}.
- temporal_scope должен быть одним из: {", ".join(sorted(ALLOWED_TEMPORAL_SCOPES))}.
- clarification.kind должен быть одним из: {", ".join(sorted(ALLOWED_CLARIFICATION_KINDS))}.
- subject — краткий нормализованный предмет вопроса, отдельно от типа запрашиваемого факта.
- Сначала определи requested_fact_type, затем temporal_scope и только после этого intent.
- Слова "заказ", "склад", "отгрузка", "оплата", "счёт" и "поставка" описывают subject
  и сами по себе не делают вопрос операционным.
- Вопросы "кто отвечает", "кто занимается", "кто ведёт", "кто руководит",
  "кто главный", "к кому обратиться" имеют статический тип ответственности и
  не относятся к one_c_operational_lookup.
- one_c_operational_lookup используй для текущего статуса, текущего значения,
  текущей цены или наличия: например, "какой статус", "оплачен ли",
  "отгружен ли", "что сейчас есть", "какая текущая цена".
- Если вопрос вне корпоративной базы, используй intent="off_topic" и answer_type="no_answer".
- Если вопрос неоднозначный про "КП", используй intent="ambiguous_abbreviation", answer_type="clarification", needs_clarification=true.
- clarification.required=true допустим только при доказанной неоднозначности или
  отсутствующем обязательном параметре. Не используй clarification как общий fallback.
- Для abbreviation/lexical_ambiguity укажи ambiguity_span, минимум два разных
  candidate_meanings и предметный question.
- Для missing_* укажи минимум один конкретный missing_slot и предметный question.
- Если уточнение не требуется, верни clarification.required=false и kind="none".
- Если вопрос про "ЦКП", используй intent="company_ckp".
- ЦКП = ценный конечный продукт. ЦКП НЕ означает "центр комплексных предложений".

Корпоративная карта смыслов:
- ЦКП = ценный конечный продукт компании.
- КП без уточнения неоднозначно: может быть коммерческое предложение, но может путаться с ЦКП.
- ЗРС относится к документу "ИП-0004 Структура ЗРС".
- Вопросы про отделы, подразделения, отделения, оргструктуру, оргсхему, "из чего состоит компания" относятся к org_structure.
- Вопросы "кто отвечает", "какой отдел занимается", "функции отдела" относятся к roles_responsibility.
- Вопросы про отпуск относятся к vacation.
- Вопросы про договоры относятся к contract_approval.
- Вопросы про командировки относятся к business_trip.
- Вопросы про распоряжения относятся к order_disposition.
- Вопросы про задачи, взять задачу в работу, направить задачу относятся к task_management.
- Вопросы про потерю документа относятся к document_loss.
- Вопросы про ноутбук, оборудование, доступ, IT-заявку относятся к equipment_it_request.
- Вопросы "чем занимается компания?", "что делает компания?", "какая цель компании?" относятся к company_identity, а не к org_structure.
- Для document_loss не используй answer_type="procedure"; используй "partial_answer" или "no_answer".
- Для equipment_it_request не используй answer_type="procedure"; если нет известного источника, preferred_sources должен быть пустым.
- Для attendance_absence, equipment_it_request, document_loss, one_c_operational_lookup и kp_commercial_offer не используй answer_type="procedure", если нет достоверного источника.
- Текущие операционные 1С-вопросы про цены, остатки, статус заказа, счета,
  контрагентов, отгрузки и номенклатуру относятся к one_c_operational_lookup.
  Вопросы о правилах, процедурах и ответственных по тем же предметам остаются
  semantic/static. Операционные запросы пока не выполняют поиск в 1С.
- Вопросы про опоздание, болезнь, отсутствие и невыход на работу относятся к attendance_absence, а не к vacation.

JSON schema:
{{
  "intent": "org_structure",
  "requested_fact_type": "list",
  "temporal_scope": "static",
  "subject": "организационная структура компании",
  "normalized_question": "Какие подразделения и отделения есть в организационной структуре компании?",
  "query_expansions": [
    "оргсхема компании",
    "организационная структура компании",
    "крупные отделения компании",
    "подразделения компании",
    "отделы компании"
  ],
  "preferred_sources": [
    "2026-03-03_Оргсхема _ Компании"
  ],
  "answer_type": "list",
  "needs_clarification": false,
  "clarification_question": null,
  "clarification": {{
    "required": false,
    "kind": "none",
    "ambiguity_span": null,
    "candidate_meanings": [],
    "missing_slots": [],
    "question": null,
    "confidence": 0.0
  }},
  "confidence": 0.9,
  "notes": "Вопрос про организационную структуру компании."
}}
""".strip()
    user_prompt = f"Вопрос пользователя: {question}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_query_plan_response(content: str, question: str) -> QueryPlan:
    """Parse a possibly messy model response into a validated QueryPlan."""
    data = _loads_json_object(content)
    return _query_plan_from_mapping(data, question)


def validate_query_plan(plan: QueryPlan, question: str) -> QueryPlan:
    """Validate a QueryPlan supplied by a deterministic or external analyzer."""
    return _sanitize_plan(plan, question)


def validate_clarification(
    question: str,
    query_plan: QueryPlan,
) -> ClarificationDecision:
    """Accept clarification only when the current question proves it is needed."""
    raw = (
        query_plan.raw_clarification
        if query_plan.raw_clarification is not None
        else _legacy_clarification_from_plan(query_plan)
    )
    normalized_question = _normalize_for_match(question)
    reasons: list[str] = []

    registry_plan = _registered_ambiguity_plan(normalized_question)
    if registry_plan is not None:
        if not raw.required:
            _append_reason(reasons, "deterministic_ambiguity_registry")
        elif raw.kind not in {"abbreviation", "lexical_ambiguity"}:
            _append_reason(reasons, "clarification_kind_corrected")
        if raw.ambiguity_span is None:
            _append_reason(reasons, "ambiguity_span_inferred_from_registry")
        if len(_distinct_values(raw.candidate_meanings)) < 2:
            _append_reason(reasons, "candidate_meanings_inferred_from_registry")
        if not _is_subjective_clarification_question(raw.question):
            _append_reason(reasons, "clarification_question_built_from_registry")
        return ClarificationDecision(
            raw=raw,
            validated=registry_plan,
            action="clarify",
            validation_reasons=reasons,
        )

    missing_slot_plan = _deterministic_missing_slot_plan(normalized_question)
    if missing_slot_plan is not None:
        if not raw.required:
            _append_reason(reasons, "deterministic_missing_required_slot")
        elif raw.kind != missing_slot_plan.kind:
            _append_reason(reasons, "clarification_kind_corrected")
        if not set(raw.missing_slots).intersection(missing_slot_plan.missing_slots):
            _append_reason(reasons, "missing_slots_inferred")
        if not _missing_slot_question_is_relevant(
            raw.question,
            missing_slot_plan.missing_slots,
        ):
            _append_reason(reasons, "clarification_question_built_for_missing_slot")
        return ClarificationDecision(
            raw=raw,
            validated=missing_slot_plan,
            action="clarify",
            validation_reasons=reasons,
        )

    if not raw.required:
        return ClarificationDecision(raw=raw)

    if raw.kind not in ALLOWED_CLARIFICATION_KINDS or raw.kind == "none":
        _append_reason(reasons, "invalid_clarification_kind")

    if raw.kind in {"abbreviation", "lexical_ambiguity"}:
        if not raw.ambiguity_span:
            _append_reason(reasons, "missing_ambiguity_span")
        elif not _span_occurs_in_question(question, raw.ambiguity_span):
            _append_reason(reasons, "ambiguity_span_not_in_question")

        meanings = _distinct_values(raw.candidate_meanings)
        if len(meanings) < 2:
            _append_reason(reasons, "insufficient_candidate_meanings")
        if not _is_subjective_clarification_question(raw.question):
            _append_reason(reasons, "invalid_clarification_question")
        elif raw.ambiguity_span and not _question_mentions_ambiguity(
            raw.question,
            raw.ambiguity_span,
            meanings,
        ):
            _append_reason(reasons, "clarification_question_not_about_ambiguity")

        if not reasons:
            return ClarificationDecision(
                raw=raw,
                validated=replace(raw, candidate_meanings=meanings),
                action="clarify",
            )
    elif raw.kind.startswith("missing_"):
        if not _distinct_values(raw.missing_slots):
            _append_reason(reasons, "missing_required_slot")
        if not _missing_slot_question_is_relevant(raw.question, raw.missing_slots):
            _append_reason(reasons, "clarification_question_not_about_missing_slot")
        _append_reason(reasons, "missing_slot_not_proven_by_question")

    if query_plan.subject:
        _append_reason(reasons, "question_is_sufficiently_specific")
    _append_reason(reasons, "invalid_clarification_rejected")
    return ClarificationDecision(
        raw=raw,
        validation_reasons=reasons,
    )


def fallback_query_plan(question: str) -> QueryPlan:
    """Return a deterministic, validated QueryPlan v2 safety net."""
    base_plan = _fallback_query_plan_base(question)
    return _sanitize_plan(base_plan, question, fallback_used=True)


def _fallback_query_plan_base(question: str) -> QueryPlan:
    """Build the legacy domain plan before QueryPlan v2 validation."""
    clean_question = question.strip()
    normalized = _normalize_for_match(clean_question)

    if _is_ckp_question(normalized):
        return QueryPlan(
            intent="company_ckp",
            normalized_question="Что такое ЦКП как ценный конечный продукт компании?",
            query_expansions=[
                "ЦКП компании",
                "ценный конечный продукт компании",
                "ИП-0003 ЦКП SERVICELINE",
            ],
            preferred_sources=["ИП-0003 ЦКП SERVICELINE"],
            answer_type="definition",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.75,
            notes="Fallback: ЦКП зафиксирован как ценный конечный продукт.",
        )

    if _is_company_identity_question(normalized):
        return _simple_plan(
            clean_question,
            intent="company_identity",
            answer_type="definition",
            query_expansions=["цели компании", "замыслы компании", "ЦКП Serviceline"],
            preferred_sources=[
                "ИП-0002 Цели и замыслы компании Serviceline",
                "ИП-0003 ЦКП SERVICELINE",
            ],
        )

    if _is_kp_commercial_offer_question(normalized):
        return QueryPlan(
            intent="kp_commercial_offer",
            normalized_question="Что известно про коммерческое предложение?",
            query_expansions=[
                "коммерческое предложение",
                "КП как коммерческое предложение",
                "отдел продаж коммерческое предложение",
            ],
            preferred_sources=[],
            answer_type="partial_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.7,
            notes="Fallback: КП явно уточнено как коммерческое предложение.",
        )

    if _is_ambiguous_kp_question(normalized):
        return QueryPlan(
            intent="ambiguous_abbreviation",
            normalized_question=clean_question,
            query_expansions=["КП", "коммерческое предложение", "ЦКП"],
            preferred_sources=[],
            answer_type="clarification",
            needs_clarification=True,
            clarification_question=(
                "Вы имеете в виду КП как коммерческое предложение или ЦКП "
                "как ценный конечный продукт компании?"
            ),
            confidence=0.8,
            notes="Fallback: аббревиатура КП без уточнения неоднозначна.",
        )

    if _is_attendance_absence_question(normalized):
        return _simple_plan(
            clean_question,
            intent="attendance_absence",
            answer_type="partial_answer",
            query_expansions=[
                "опоздание на работу",
                "отсутствие на работе",
                "болезнь сотрудника",
            ],
        )

    if _is_one_c_operational_lookup_question(normalized):
        return _simple_plan(
            clean_question,
            intent="one_c_operational_lookup",
            answer_type="partial_answer",
            query_expansions=[
                "операционный запрос 1С",
                "статус заказа",
                "цены остатки счета контрагенты",
            ],
        )

    if _contains_any(
        normalized,
        (
            "отделы",
            "отделов",
            "отделения",
            "отделений",
            "подразделения",
            "подразделений",
            "подразделен",
            "оргсхема",
            "оргструктура",
            "организационная структура",
            "структура компании",
            "из чего состоит компания",
            "что входит в структуру компании",
            "устроена компания",
            "службы",
        ),
    ):
        return QueryPlan(
            intent="org_structure",
            normalized_question=(
                "Какие подразделения и отделения есть в организационной "
                "структуре компании?"
            ),
            query_expansions=[
                "оргсхема компании",
                "организационная структура компании",
                "подразделения компании",
                "отделы компании",
            ],
            preferred_sources=["2026-03-03_Оргсхема _ Компании"],
            answer_type="list",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.75,
            notes="Fallback: вопрос про организационную структуру.",
        )

    if _is_roles_responsibility_question(normalized):
        return _roles_responsibility_plan(clean_question)

    if _contains_any(normalized, ("отпуск", "отпуска", "отпускной")):
        return _simple_plan(
            clean_question,
            intent="vacation",
            answer_type="procedure",
            query_expansions=["оформить отпуск", "отпуск в документообороте"],
        )

    if "зрс" in normalized or "ситуация данные решение" in normalized:
        if _contains_any(normalized, ("соглас", "утверд", "одобр", "подпис")):
            return _simple_plan(
                clean_question,
                intent="zrs_approval",
                answer_type="procedure",
                query_expansions=["согласование ЗРС", "ИП-0004 Структура ЗРС"],
                preferred_sources=["ИП-0004 Структура ЗРС"],
            )
        return _simple_plan(
            clean_question,
            intent="zrs_definition",
            answer_type="definition",
            query_expansions=["что такое ЗРС", "ИП-0004 Структура ЗРС"],
            preferred_sources=["ИП-0004 Структура ЗРС"],
        )

    if _contains_any(normalized, ("договор", "контракт")):
        return _simple_plan(
            clean_question,
            intent="contract_approval",
            answer_type="procedure",
            query_expansions=["согласование договора", "договор в документообороте"],
        )

    if _contains_any(
        normalized,
        (
            "потерял документ",
            "потерял оригинал документа",
            "потеряла документ",
            "потерян документ",
            "утерян документ",
            "пропал документ",
            "не могу найти документ",
        ),
    ):
        return QueryPlan(
            intent="document_loss",
            normalized_question="Что делать, если потерян документ?",
            query_expansions=["потерян документ", "утерян документ", "документ потеряли"],
            preferred_sources=[],
            answer_type="partial_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.7,
            notes="Fallback: вопрос про потерю документа.",
        )

    if _contains_any(
        normalized,
        (
            "задача",
            "задачу",
            "задачи",
            "задачей",
            "взять задачу",
            "направить задачу",
            "поставить задачу",
        ),
    ):
        return _simple_plan(
            clean_question,
            intent="task_management",
            answer_type="procedure",
            query_expansions=["работа с задачами", "взять задачу в работу"],
        )

    if _contains_any(
        normalized,
        (
            "документооборот",
            "1с до",
            "1с документооборот",
            "создать документ",
            "согласовать документ",
        ),
    ):
        return _simple_plan(
            clean_question,
            intent="document_flow",
            answer_type="procedure",
            query_expansions=["документооборот", "1С ДО", "согласование документов"],
            preferred_sources=["ИП-0006 Документооборот"],
        )

    if _contains_any(normalized, ("командиров", "служебная поездка")):
        return _simple_plan(
            clean_question,
            intent="business_trip",
            answer_type="procedure",
            query_expansions=["согласование командировки", "оформить командировку"],
        )

    if _contains_any(normalized, ("распоряжение", "распоряжения", "распоряжении")):
        return _simple_plan(
            clean_question,
            intent="order_disposition",
            answer_type="procedure",
            query_expansions=["распоряжения", "ИП-0005 Распоряжения"],
        )

    if _contains_any(
        normalized,
        (
            "задача",
            "задачу",
            "задачи",
            "задачей",
            "взять задачу",
            "направить задачу",
            "поставить задачу",
        ),
    ):
        return _simple_plan(
            clean_question,
            intent="task_management",
            answer_type="procedure",
            query_expansions=["работа с задачами", "взять задачу в работу"],
        )

    if _contains_any(
        normalized,
        (
            "потерял документ",
            "потерял оригинал документа",
            "потеряла документ",
            "потерян документ",
            "утерян документ",
            "пропал документ",
            "не могу найти документ",
        ),
    ):
        return QueryPlan(
            intent="document_loss",
            normalized_question="Что делать, если потерян документ?",
            query_expansions=["потерян документ", "утерян документ", "документ потеряли"],
            preferred_sources=[],
            answer_type="partial_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.7,
            notes="Fallback: вопрос про потерю документа.",
        )

    if _contains_any(
        normalized,
        (
            "ноутбук",
            "оборудование",
            "доступ",
            "it-заяв",
            "it заяв",
            "айти",
            "компьютер",
            "программа",
            "программу",
            "пароль",
        ),
    ):
        return _simple_plan(
            clean_question,
            intent="equipment_it_request",
            answer_type="general",
            query_expansions=["получить ноутбук", "заявка на оборудование", "IT-заявка"],
        )

    if _is_obvious_off_topic(normalized):
        return QueryPlan(
            intent="off_topic",
            normalized_question=clean_question,
            query_expansions=[],
            preferred_sources=[],
            answer_type="no_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.85,
            notes="Fallback: вопрос вне корпоративной базы знаний.",
        )

    return QueryPlan(
        intent="unknown",
        normalized_question=clean_question,
        query_expansions=[],
        preferred_sources=[],
        answer_type="general",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.0,
        notes="Fallback: intent не определен.",
    )


def _query_plan_from_mapping(data: dict[str, Any], question: str) -> QueryPlan:
    raw_intent = _coerce_optional_str(data.get("intent"))
    intent = raw_intent
    if intent not in ALLOWED_INTENTS:
        intent = "unknown"

    answer_type = data.get("answer_type")
    if not isinstance(answer_type, str) or answer_type not in ALLOWED_ANSWER_TYPES:
        answer_type = "general"

    raw_requested_fact_type = _coerce_optional_str(data.get("requested_fact_type"))
    requested_fact_type = (
        raw_requested_fact_type
        if raw_requested_fact_type in ALLOWED_REQUESTED_FACT_TYPES
        else "unknown"
    )
    raw_temporal_scope = _coerce_optional_str(data.get("temporal_scope"))
    temporal_scope = (
        raw_temporal_scope
        if raw_temporal_scope in ALLOWED_TEMPORAL_SCOPES
        else "unknown"
    )
    raw_subject = _coerce_optional_str(data.get("subject"))
    normalized_question = _coerce_str(data.get("normalized_question")) or question
    query_expansions = _coerce_str_list(data.get("query_expansions"))
    preferred_sources = _coerce_str_list(data.get("preferred_sources"))
    needs_clarification = _coerce_bool(data.get("needs_clarification"))
    clarification_question = _coerce_optional_str(data.get("clarification_question"))
    raw_clarification = _clarification_from_mapping(
        data.get("clarification"),
        legacy_required=needs_clarification,
        legacy_question=clarification_question,
    )
    confidence = _coerce_confidence(data.get("confidence"))
    notes = _coerce_optional_str(data.get("notes"))

    if intent == "ambiguous_abbreviation":
        answer_type = "clarification"

    if intent == "off_topic":
        answer_type = "no_answer"

    plan = QueryPlan(
        intent=intent,
        normalized_question=normalized_question,
        query_expansions=query_expansions,
        preferred_sources=preferred_sources,
        answer_type=answer_type,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        confidence=confidence,
        notes=notes,
        requested_fact_type=requested_fact_type,
        temporal_scope=temporal_scope,
        subject=_normalize_subject(raw_subject or ""),
        raw_intent=raw_intent,
        raw_requested_fact_type=raw_requested_fact_type,
        raw_temporal_scope=raw_temporal_scope,
        raw_subject=raw_subject,
        raw_clarification=raw_clarification,
    )
    return _sanitize_plan(plan, question)


def _sanitize_plan(
    plan: QueryPlan,
    question: str,
    *,
    fallback_used: bool = False,
) -> QueryPlan:
    normalized_question = _normalize_for_match(question)
    base_plan = plan

    if _is_ambiguous_kp_question(normalized_question):
        base_plan = _carry_raw_diagnostics(_fallback_query_plan_base(question), plan)
    elif _is_kp_commercial_offer_question(normalized_question):
        base_plan = _carry_raw_diagnostics(_fallback_query_plan_base(question), plan)
    elif _is_attendance_absence_question(normalized_question):
        base_plan = _carry_raw_diagnostics(_fallback_query_plan_base(question), plan)
    elif _is_company_identity_question(normalized_question):
        base_plan = _carry_raw_diagnostics(_fallback_query_plan_base(question), plan)
    elif _is_roles_responsibility_question(normalized_question):
        base_plan = _carry_raw_diagnostics(_roles_responsibility_plan(question), plan)
    elif plan.intent == "company_ckp" or _is_ckp_question(normalized_question):
        base_plan = _sanitize_ckp_plan(plan)

    validated_plan = _validate_query_plan_v2(
        base_plan,
        question,
        fallback_used=fallback_used,
    )
    answer_type = validated_plan.answer_type
    preferred_sources = _filter_compatible_sources(
        validated_plan.preferred_sources,
        validated_plan.intent,
    )

    if validated_plan.intent == "document_loss":
        if answer_type == "procedure":
            answer_type = "partial_answer"
        preferred_sources = []

    if validated_plan.intent == "equipment_it_request":
        if answer_type == "procedure":
            answer_type = "general"
        preferred_sources = []

    if validated_plan.intent in {
        "one_c_operational_lookup",
        "kp_commercial_offer",
        "attendance_absence",
    }:
        if answer_type == "procedure" and not preferred_sources:
            answer_type = "partial_answer"

    prepared_plan = replace(
        validated_plan,
        preferred_sources=preferred_sources,
        answer_type=answer_type,
    )
    clarification_decision = validate_clarification(question, prepared_plan)
    validated_clarification = clarification_decision.validated
    if clarification_decision.action == "clarify":
        answer_type = "clarification"
        intent = (
            "ambiguous_abbreviation"
            if validated_clarification.kind
            in {"abbreviation", "lexical_ambiguity"}
            else "unknown"
        )
        operational_lookup = False
        operational_decision_reason = "clarification_required_before_routing"
        validation_reasons = list(prepared_plan.validation_reasons)
        _append_reason(validation_reasons, "clarification_routing_deferred")
    elif answer_type == "clarification":
        answer_type = "general"
        intent = prepared_plan.intent
        operational_lookup = prepared_plan.operational_lookup
        operational_decision_reason = prepared_plan.operational_decision_reason
        validation_reasons = list(prepared_plan.validation_reasons)
    else:
        intent = prepared_plan.intent
        operational_lookup = prepared_plan.operational_lookup
        operational_decision_reason = prepared_plan.operational_decision_reason
        validation_reasons = list(prepared_plan.validation_reasons)

    return replace(
        prepared_plan,
        intent=intent,
        answer_type=answer_type,
        operational_lookup=operational_lookup,
        operational_decision_reason=operational_decision_reason,
        validation_reasons=validation_reasons,
        needs_clarification=validated_clarification.required,
        clarification_question=validated_clarification.question,
        clarification=validated_clarification,
        raw_clarification=clarification_decision.raw,
        clarification_action=clarification_decision.action,
        clarification_validation_reasons=list(
            clarification_decision.validation_reasons
        ),
    )


def _validate_query_plan_v2(
    plan: QueryPlan,
    question: str,
    *,
    fallback_used: bool,
) -> QueryPlan:
    """Make fact type and time scope authoritative over subject keywords."""
    normalized_question = _normalize_for_match(question)
    reasons = list(plan.validation_reasons)
    if fallback_used:
        _append_reason(reasons, "fallback_plan")

    raw_requested_fact_type = plan.raw_requested_fact_type
    if (
        raw_requested_fact_type is not None
        and raw_requested_fact_type not in ALLOWED_REQUESTED_FACT_TYPES
    ):
        _append_reason(reasons, "invalid_requested_fact_type")
    raw_temporal_scope = plan.raw_temporal_scope
    if (
        raw_temporal_scope is not None
        and raw_temporal_scope not in ALLOWED_TEMPORAL_SCOPES
    ):
        _append_reason(reasons, "invalid_temporal_scope")

    prior_resolution = plan.fact_type_resolution
    initial_candidate = (
        prior_resolution.initial_fact_type
        if (
            prior_resolution.decision_reasons
            or prior_resolution.matched_signals
            or prior_resolution.rejected_candidates
        )
        else plan.requested_fact_type
    )
    initial_requested_fact_type = (
        initial_candidate
        if initial_candidate in ALLOWED_REQUESTED_FACT_TYPES
        else "unknown"
    )

    subject = _normalize_subject(plan.subject)
    subject_was_inferred = False
    if not subject:
        subject = _infer_subject(normalized_question)
        subject_was_inferred = bool(subject)

    fact_type_resolution = RequestedFactTypeResolver().resolve(
        normalized_question=normalized_question,
        intent=plan.intent,
        subject=subject,
        entities=(),
        answer_shape=plan.answer_type,
        draft_requested_fact_type=initial_requested_fact_type,
        metadata={
            "raw_requested_fact_type": raw_requested_fact_type,
            "raw_intent": plan.raw_intent,
        },
    )
    requested_fact_type = fact_type_resolution.resolved_fact_type
    if (
        fact_type_resolution.resolution_status
        in {"resolved_from_structure", "resolved_from_intent"}
        and requested_fact_type != initial_requested_fact_type
    ):
        for reason in fact_type_resolution.decision_reasons:
            _append_reason(reasons, reason)
    elif fact_type_resolution.resolution_status == "ambiguous":
        _append_reason(reasons, "requested_fact_type_ambiguous")

    temporal_scope = (
        plan.temporal_scope
        if plan.temporal_scope in ALLOWED_TEMPORAL_SCOPES
        else "unknown"
    )
    inferred_temporal_scope, temporal_reason = _infer_temporal_scope(
        normalized_question,
        requested_fact_type,
    )
    if inferred_temporal_scope != "unknown" and temporal_scope != inferred_temporal_scope:
        temporal_scope = inferred_temporal_scope
        _append_reason(reasons, temporal_reason)
    if subject_was_inferred:
        _append_reason(reasons, "subject_inferred")

    operational_lookup, operational_reason = _operational_decision(
        requested_fact_type,
        temporal_scope,
        plan.intent,
    )
    intent = plan.intent if plan.intent in ALLOWED_INTENTS else "unknown"
    if operational_lookup and intent != "one_c_operational_lookup":
        intent = "one_c_operational_lookup"
        _append_reason(reasons, "intent_corrected_to_one_c_operational_lookup")
    elif requested_fact_type in {
        "responsible_person",
        "primary_contact",
        "unit_head",
    } and intent != "roles_responsibility":
        intent = "roles_responsibility"
        _append_reason(reasons, "intent_corrected_to_roles_responsibility")
    elif (
        not operational_lookup
        and intent == "one_c_operational_lookup"
        and requested_fact_type in STATIC_FACT_TYPES
    ):
        intent = "unknown"
        _append_reason(reasons, "static_fact_cleared_operational_intent")
    elif temporal_scope == "historical" and intent == "one_c_operational_lookup":
        intent = "unknown"
        _append_reason(reasons, "historical_not_routed_as_current")

    return replace(
        plan,
        intent=intent,
        requested_fact_type=requested_fact_type,
        temporal_scope=temporal_scope,
        subject=subject,
        operational_lookup=operational_lookup,
        operational_decision_reason=operational_reason,
        validation_reasons=reasons,
        fact_type_resolution=fact_type_resolution,
    )


def _carry_raw_diagnostics(target: QueryPlan, source: QueryPlan) -> QueryPlan:
    """Keep the analyzer proposal visible when a deterministic rule replaces it."""
    return replace(
        target,
        requested_fact_type=source.requested_fact_type,
        temporal_scope=source.temporal_scope,
        subject=source.subject,
        raw_intent=source.raw_intent,
        raw_requested_fact_type=source.raw_requested_fact_type,
        raw_temporal_scope=source.raw_temporal_scope,
        raw_subject=source.raw_subject,
        validation_reasons=list(source.validation_reasons),
        raw_clarification=(
            source.raw_clarification
            if source.raw_clarification is not None
            else _legacy_clarification_from_plan(source)
        ),
    )


def _infer_requested_fact_type(
    question: str,
    plan: QueryPlan,
) -> tuple[str, str]:
    if _contains_any(
        question,
        (
            "кто руководит",
            "кто главный",
            "кто начальник",
            "руководитель какого",
        ),
    ):
        return "unit_head", "explicit_unit_head_question"
    if _contains_any(
        question,
        (
            "к кому обратиться",
            "к кому обращаться",
            "к кому идти",
            "кто контактное лицо",
            "контактное лицо",
        ),
    ):
        return "primary_contact", "explicit_primary_contact_question"
    if _is_explicit_responsibility_question(question):
        return "responsible_person", "explicit_responsibility_question"
    if _contains_any(
        question,
        (
            "кому подавать",
            "кому подать",
            "кому отдать",
            "куда подавать",
            "куда подать",
            "куда отдать",
        ),
    ):
        return "document_recipient", "explicit_document_recipient_question"
    if _is_procedure_question(question):
        return "procedure", "explicit_procedure_question"
    if _contains_any(question, ("что такое", "что означает", "что значит")):
        return "definition", "explicit_definition_question"
    if _contains_any(
        question,
        (
            "чем отличается",
            "сравни",
            "разница между",
            "одно и то же",
            "то же самое",
        ),
    ):
        return "comparison", "explicit_comparison_question"
    if _contains_any(
        question,
        (
            "какие правила",
            "какие подразделения",
            "какие отделы",
            "перечисли",
            "список ",
            "что у нас по ",
        ),
    ):
        return "list", "explicit_list_question"
    if _is_current_status_question(question):
        return "current_status", "explicit_current_status_question"
    if _is_availability_question(question):
        return "availability", "explicit_availability_question"
    if _is_price_question(question):
        return "price", "explicit_price_question"
    if _is_current_value_question(question):
        return "current_value", "explicit_current_value_question"
    return "unknown", ""


def _fact_type_from_plan(plan: QueryPlan) -> str:
    if plan.intent == "roles_responsibility":
        return "responsible_person"
    if plan.intent == "one_c_operational_lookup":
        return "current_value"
    if plan.answer_type in {"definition", "procedure", "list", "comparison"}:
        return plan.answer_type
    return "unknown"


def _infer_temporal_scope(
    question: str,
    requested_fact_type: str,
) -> tuple[str, str]:
    if _contains_any(
        question,
        (
            "в прошлом",
            "прошлый заказ",
            "прошлом заказе",
            "раньше",
            "историческ",
            "была цена",
            "был статус",
        ),
    ):
        return "historical", "explicit_historical_scope"
    if requested_fact_type in STATIC_FACT_TYPES:
        return "static", "static_fact_type"
    if requested_fact_type in CURRENT_OPERATIONAL_FACT_TYPES:
        return "current", "current_operational_fact_type"
    if _contains_any(
        question,
        (
            "сейчас",
            "текущ",
            "на данный момент",
            "уже",
            "сегодня",
        ),
    ):
        return "current", "explicit_current_scope"
    return "unknown", ""


def _operational_decision(
    requested_fact_type: str,
    temporal_scope: str,
    intent: str,
) -> tuple[bool, str]:
    if temporal_scope == "historical":
        return False, "historical_scope_not_current_lookup"
    if (
        requested_fact_type in CURRENT_OPERATIONAL_FACT_TYPES
        and temporal_scope == "current"
    ):
        return True, "current_operational_fact_type"
    if requested_fact_type in STATIC_FACT_TYPES:
        return False, "static_requested_fact_type"
    if requested_fact_type == "unknown" and intent == "one_c_operational_lookup":
        return True, "legacy_operational_intent"
    return False, "no_current_operational_signal"


def _is_procedure_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "как оформить",
            "как получить",
            "как проходит",
            "как согласовать",
            "как создать",
            "как подать",
            "как завести",
            "как запросить",
            "как установить",
            "что делать",
            "порядок ",
        ),
    )


def _is_explicit_responsibility_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "кто отвечает",
            "кто занимается",
            "кто ведет",
            "чья ответственность",
            "кто выпускает",
            "какой отдел занимается",
        ),
    ) or re.search(r"\bкто\b[^?]{0,40}\bзанимается\b", question) is not None


def _is_current_status_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "какой статус",
            "статус заказа",
            "на каком этапе",
            "отгружен ли",
            "отгрузили ли",
            "оплачен ли",
            "согласуют мое уже поданное",
            "согласуют моё уже поданное",
            "когда поставщик отгрузит",
            "какая отгрузка по заказу",
        ),
    )


def _is_availability_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "что сейчас есть",
            "есть ли остатки",
            "остатки на складе",
            "в наличии",
            "наличие ",
            "покажи остатки",
        ),
    )


def _is_price_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "текущая цена",
            "текущую цену",
            "какая цена",
            "найди цену",
            "цена",
            "цену",
            "стоимость детали",
            "сколько сейчас стоит",
        ),
    )


def _is_current_value_question(question: str) -> bool:
    if _contains_any(
        question,
        (
            "найди контрагента",
            "найди деталь",
            "покажи счета",
            "покажи номенклатуру",
        ),
    ):
        return True
    return (
        _contains_any(question, ("сейчас", "текущ", "на данный момент"))
        and _has_operational_subject(question)
    )


def _has_operational_subject(question: str) -> bool:
    return _contains_any(
        question,
        (
            "заказ",
            "склад",
            "отгруз",
            "оплат",
            "счет",
            "постав",
            "контрагент",
            "номенклатур",
            "цен",
            "остат",
        ),
    )


def _infer_subject(question: str) -> str:
    value = question
    prefixes = (
        r"^кто\s+(?:отвечает|занимается|ведет|руководит|главный|выпускает)\s+(?:за\s+|по\s+)?",
        r"^к\s+кому\s+обратиться\s+(?:по\s+)?",
        r"^кому\s+(?:подавать|подать|отдать)\s+",
        r"^какой\s+(?:сейчас\s+)?статус\s+",
        r"^что\s+сейчас\s+есть\s+(?:на|в)\s+",
        r"^как\s+(?:оформить|получить|проходит|согласовать|создать)\s+",
        r"^(?:отгружен|оплачен)\s+ли\s+(?:уже\s+)?",
        r"^какая\s+(?:текущая\s+)?",
        r"^какие\s+",
        r"^что\s+такое\s+",
    )
    for pattern in prefixes:
        updated = re.sub(pattern, "", value, count=1)
        if updated != value:
            value = updated
            break
    return _normalize_subject(value)


def _normalize_subject(value: str) -> str:
    normalized = _normalize_for_match(value)
    normalized = re.sub(r"[?!.,;:]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _registered_ambiguity_plan(question: str) -> ClarificationPlan | None:
    if not _is_ambiguous_kp_question(question):
        return None
    meanings = list(AMBIGUITY_REGISTRY["КП"])
    return ClarificationPlan(
        required=True,
        kind="abbreviation",
        ambiguity_span="КП",
        candidate_meanings=meanings,
        question=(
            "Вы имеете в виду КП как коммерческое предложение "
            "или как ценный конечный продукт?"
        ),
        confidence=1.0,
    )


def _deterministic_missing_slot_plan(question: str) -> ClarificationPlan | None:
    if re.search(r"\b(кто|кому|куда)\b[^?]{0,30}\b(этим|это|этого)\b", question):
        return ClarificationPlan(
            required=True,
            kind="missing_subject",
            missing_slots=["subject"],
            question="Что именно вы имеете в виду под «этим»?",
            confidence=1.0,
        )

    application_is_vague = (
        re.search(
            r"\b(?:куда|кому)\s+(?:направить|отправить|подать|подавать)\s+"
            r"(?:мое\s+|моё\s+)?заявлени\w*\s*[?!.,]*$",
            question,
        )
        is not None
        or re.search(
            r"\bкто\s+согласу\w+\s+(?:мое\s+|моё\s+)?заявлени\w*\s*[?!.,]*$",
            question,
        )
        is not None
    )
    if application_is_vague:
        return ClarificationPlan(
            required=True,
            kind="missing_document_type",
            missing_slots=["application_type"],
            question="Какое именно заявление вы имеете в виду?",
            confidence=1.0,
        )

    document_recipient = re.search(
        r"\b(?:куда|кому)\s+(?:отдать|передать|направить|отправить|подать|подавать)\b",
        question,
    )
    generic_documents = re.search(r"\b(?:документ\w*|бумаг\w*)\b", question)
    specific_document_type = re.search(
        r"\b(?:кадров\w*|бухгалтер\w*|внутренн\w*|оригинал\w*|эдо|"
        r"договор\w*|командиров\w*|отпуск\w*|оборудован\w*)\b",
        question,
    )
    if document_recipient and generic_documents and not specific_document_type:
        return ClarificationPlan(
            required=True,
            kind="missing_document_type",
            missing_slots=["document_type"],
            question="Какие именно документы вы имеете в виду?",
            confidence=1.0,
        )
    return None


def _span_occurs_in_question(question: str, span: str) -> bool:
    return (
        re.search(
            rf"(?<!\w){re.escape(span)}(?!\w)",
            question,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _distinct_values(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean_value = value.strip()
        key = clean_value.casefold()
        if clean_value and key not in seen:
            seen.add(key)
            result.append(clean_value)
    return result


def _is_subjective_clarification_question(question: str | None) -> bool:
    if not question or not question.strip():
        return False
    normalized = _normalize_for_match(question).strip(" ?!.,:")
    return normalized not in {
        "уточните",
        "уточните вопрос",
        "уточните пожалуйста",
        "поясните",
        "нужны уточнения",
    }


def _question_mentions_ambiguity(
    question: str,
    span: str,
    meanings: list[str],
) -> bool:
    normalized = _normalize_for_match(question)
    return _span_occurs_in_question(question, span) or any(
        _normalize_for_match(meaning) in normalized for meaning in meanings
    )


def _missing_slot_question_is_relevant(
    question: str | None,
    missing_slots: list[str],
) -> bool:
    if not _is_subjective_clarification_question(question):
        return False
    normalized = _normalize_for_match(question or "")
    for slot in _distinct_values(missing_slots):
        if slot == "subject" and _contains_any(
            normalized,
            ("что именно", "о чем", "предмет", "под «этим»", "под этим"),
        ):
            return True
        if slot == "document_type" and _contains_any(
            normalized,
            ("какие", "какой документ", "тип документ", "документ"),
        ):
            return True
        if slot == "application_type" and _contains_any(
            normalized,
            ("какое", "какой тип заявления", "заявлен"),
        ):
            return True
        if slot in normalized:
            return True
    return False


def _sanitize_ckp_plan(plan: QueryPlan) -> QueryPlan:
    """Force the corporate meaning of CKP and its known source title."""

    query_expansions = [
        _FORBIDDEN_CKP_MEANING_RE.sub("ценный конечный продукт", value)
        for value in plan.query_expansions
    ]
    if not any("ценный конечный продукт" in value.casefold() for value in query_expansions):
        query_expansions.append("ценный конечный продукт компании")

    preferred_sources = ["ИП-0003 ЦКП SERVICELINE"]

    return replace(
        plan,
        intent="company_ckp",
        normalized_question=_FORBIDDEN_CKP_MEANING_RE.sub(
            "ценный конечный продукт",
            plan.normalized_question,
        ),
        query_expansions=query_expansions,
        preferred_sources=preferred_sources,
        answer_type=plan.answer_type if plan.answer_type in {"definition", "general"} else "definition",
        needs_clarification=False,
        clarification_question=None,
        notes=(
            _FORBIDDEN_CKP_MEANING_RE.sub("ценный конечный продукт", plan.notes)
            if plan.notes is not None
            else plan.notes
        ),
    )


def _with_compatible_sources(plan: QueryPlan) -> QueryPlan:
    return replace(
        plan,
        preferred_sources=_filter_compatible_sources(plan.preferred_sources, plan.intent),
    )


def _filter_compatible_sources(sources: list[str], intent: str) -> list[str]:
    known_sources = [source for source in sources if source in KNOWN_SOURCE_TITLES]
    if intent not in INTENT_SOURCE_COMPATIBILITY:
        return known_sources

    compatible_sources = INTENT_SOURCE_COMPATIBILITY[intent]
    if not compatible_sources:
        return []
    return [source for source in known_sources if source in compatible_sources]


def _loads_json_object(content: str) -> dict[str, Any]:
    candidates = [content.strip()]

    block_match = _JSON_BLOCK_RE.search(content)
    if block_match:
        candidates.append(block_match.group(1).strip())

    extracted = _extract_first_json_object(content)
    if extracted:
        candidates.append(extracted)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    raise ValueError("model response does not contain a valid JSON object")


def _extract_first_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]

    return None


def _simple_plan(
    question: str,
    *,
    intent: str,
    answer_type: str,
    query_expansions: list[str],
    preferred_sources: list[str] | None = None,
) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        normalized_question=question,
        query_expansions=query_expansions,
        preferred_sources=preferred_sources or [],
        answer_type=answer_type,
        needs_clarification=False,
        clarification_question=None,
        confidence=0.65,
        notes=f"Fallback: распознан intent {intent}.",
    )


def _roles_responsibility_plan(question: str) -> QueryPlan:
    return _simple_plan(
        question,
        intent="roles_responsibility",
        answer_type="list",
        query_expansions=[
            "руководитель подразделения",
            "ответственный сотрудник",
            "руководитель закупок",
            "контакт ответственного",
        ],
    )


def _load_analyzer_model() -> str:
    return (
        os.getenv("OLLAMA_ANALYZER_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or DEFAULT_ANALYZER_MODEL
    )


def _is_ckp_question(question: str) -> bool:
    return "цкп" in question or _contains_any(
        question,
        (
            "ценный конечный продукт",
            "ценного конечного продукта",
            "ценному конечному продукту",
            "ценным конечным продуктом",
            "главный продукт компании",
            "главный продукт serviceline",
        ),
    )


def _is_company_identity_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "чем занимается компания",
            "что делает компания",
            "какая цель компании",
            "какая основная цель компании",
            "цель компании",
            "какой бизнес у serviceline",
            "смысл деятельности компании",
            "для чего существует компания",
            "о компании",
            "расскажи кратко о компании",
            "что такое serviceline",
            "что такое сервислайн",
        ),
    )


def _is_roles_responsibility_question(question: str) -> bool:
    return _is_explicit_responsibility_question(question) or _contains_any(
        question,
        (
            "кто руководит",
            "кто главный",
            "начальник",
            "руководитель",
            "к кому обратиться",
            "к кому обращаться",
            "к кому идти",
            "кому направить",
            "контакт",
            "телефон",
            "какой отдел занимается",
            "функции отдела",
            "ответственный",
        ),
    )


def _is_attendance_absence_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "опоздал",
            "опоздание",
            "заболел",
            "болею",
            "не вышел",
            "не выйду",
            "отсутствие",
            "не могу выйти на работу",
            "пропустил работу",
            "больничный",
        ),
    )


def _is_one_c_operational_lookup_question(question: str) -> bool:
    if _is_roles_responsibility_question(question) or _is_procedure_question(question):
        return False
    return (
        _is_current_status_question(question)
        or _is_availability_question(question)
        or _is_price_question(question)
        or _is_current_value_question(question)
    )


def _is_kp_commercial_offer_question(question: str) -> bool:
    return "коммерческое предложение" in question or (
        _KP_RE.search(question) is not None
        and _contains_any(
            question,
            (
                "кп как",
                "составить кп",
                "подготовить кп",
                "сделать кп",
                "сформируй кп",
                "кп клиенту",
                "кп по заявке",
                "коммерческого предложения",
                "коммерческому предложению",
                "коммерческим предложением",
                "коммерческих предложений",
            ),
        )
    )


def _is_ambiguous_kp_question(question: str) -> bool:
    return (
        _KP_RE.search(question) is not None
        and not _is_ckp_question(question)
        and not _is_kp_commercial_offer_question(question)
    )


def _is_obvious_off_topic(question: str) -> bool:
    return _contains_any(
        question,
        (
            "как приготовить",
            "рецепт",
            "борщ",
            "погода",
            "курс доллара",
            "выиграл вчера матч",
            "утят",
            "кот",
            "стих",
            "котлет",
            "фильм",
            "музыка",
        ),
    )


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _normalize_for_match(value: str) -> str:
    return value.casefold().replace("ё", "е")


def _coerce_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean_value = value.strip()
    return clean_value or None


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _clarification_from_mapping(
    value: Any,
    *,
    legacy_required: bool,
    legacy_question: str | None,
) -> ClarificationPlan:
    if not isinstance(value, dict):
        return ClarificationPlan(
            required=legacy_required,
            question=legacy_question,
        )
    return ClarificationPlan(
        required=(
            _coerce_bool(value.get("required"))
            if "required" in value
            else legacy_required
        ),
        kind=_coerce_optional_str(value.get("kind")) or "none",
        ambiguity_span=_coerce_optional_str(value.get("ambiguity_span")),
        candidate_meanings=_coerce_str_list(value.get("candidate_meanings")),
        missing_slots=_coerce_str_list(value.get("missing_slots")),
        question=(
            _coerce_optional_str(value.get("question"))
            if "question" in value
            else legacy_question
        ),
        confidence=_coerce_confidence(value.get("confidence")),
    )


def _legacy_clarification_from_plan(plan: QueryPlan) -> ClarificationPlan:
    return ClarificationPlan(
        required=plan.needs_clarification,
        question=plan.clarification_question,
        confidence=plan.confidence if plan.needs_clarification else 0.0,
    )


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "да"}
    return bool(value)


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))
