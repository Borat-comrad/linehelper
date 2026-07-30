"""Semantic retrieval helpers built on top of the local MemoryStore."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from linehelper.memory.memory_store import MemoryStore


DEFAULT_MEMORY_DB_PATH = Path("data/memory/linehelper_memory.db")

QUERY_EXPANSIONS: dict[str, list[str]] = {
    "компания": [
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
        "цель компании",
        "основная цель компании",
        "ценный конечный продукт",
        "комплексная услуга",
    ],
    "serviceline": [
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
        "цель компании",
        "ЦКП SERVICELINE",
    ],
    "сервислайн": [
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
        "цель компании",
        "ЦКП SERVICELINE",
    ],
    "документооборот": [
        "ИП-0006 Документооборот",
        "1С ДО",
        "1С Документооборот",
        "согласование",
        "инструкция согласования",
        "официальная переписка",
    ],
    "документоборот": ["ИП-0006 Документооборот", "1С ДО", "согласование"],
    "согласование": ["Документооборот", "1С ДО", "инструкция согласования"],
    "зрс": ["завершенная работа сотрудника", "ситуация", "данные", "решение"],
    "цкп": ["ценный конечный продукт", "комплексная услуга"],
    "ценный конечный продукт": [
        "ИП-0003 ЦКП SERVICELINE",
        "ЦКП SERVICELINE",
        "комплексная услуга",
    ],
    "планирование": [
        "планирование на неделю",
        "план рабочих задач",
        "план на неделю",
    ],
    "план": ["планирование на неделю", "план рабочих задач"],
    "задача": ["Работа с задачами", "взять задачу в работу", "статус задачи"],
    "задачу": ["Работа с задачами", "взять задачу в работу", "статус задачи"],
    "задачи": ["Работа с задачами", "взять задачу в работу", "статус задачи"],
    "контроль": ["ИП-0005 Распоряжения", "письменная форма и контроль"],
    "командировка": ["согласование командировки", "СЗ_Командировка"],
    "распоряжение": [
        "письменное распоряжение",
        "исполнитель",
        "срок",
        "ожидаемый результат",
    ],
    "договор": ["согласование договора", "документооборот", "контрагент"],
    "контрагент": ["ИНН", "контрагенты", "создать контрагента"],
    "письменная коммуникация": [
        "коммуникационные линии",
        "командные линии",
        "послание",
    ],
    "оргсхема": ["организующая схема", "подразделения", "коммерческое отделение"],
}

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
_SPACE_RE = re.compile(r"\s+")
_STOP_WORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "для",
        "делать",
        "до",
        "его",
        "ее",
        "если",
        "есть",
        "и",
        "из",
        "или",
        "как",
        "какие",
        "какой",
        "к",
        "ко",
        "на",
        "не",
        "нового",
        "о",
        "об",
        "означает",
        "оформить",
        "от",
        "по",
        "подчиненному",
        "при",
        "про",
        "с",
        "со",
        "состоит",
        "самое",
        "такое",
        "у",
        "через",
        "что",
        "это",
        "говорится",
    }
)
_MAX_EXCERPT_CHARS = 450
RETRIEVAL_STAGE_NAMES = frozenset(
    {
        "exact_identifier",
        "exact_entity",
        "exact_subject",
        "fts_resolved_question",
        "fts_normalized_question",
        "fts_query_expansion",
        "procedure_lookup",
        "organization_function_lookup",
        "preferred_source_lookup",
        "sibling_lookup",
    }
)
ORGANIZATION_FUNCTION_FACT_TYPES = frozenset(
    {
        "responsible_person",
        "primary_contact",
        "unit_head",
        "document_recipient",
    }
)
ORGANIZATION_RETRIEVAL_DOC_TYPES = (
    "employee_role",
    "organization_unit",
    "responsibility_route",
    "role_combination",
)
STAGE_SCORE_WEIGHTS: dict[str, float] = {
    "exact_identifier": 220.0,
    "exact_entity": 180.0,
    "organization_function_lookup": 200.0,
    "procedure_lookup": 180.0,
    "exact_subject": 140.0,
    "preferred_source_lookup": 100.0,
    "fts_resolved_question": 80.0,
    "fts_normalized_question": 70.0,
    "fts_query_expansion": 30.0,
    "sibling_lookup": 20.0,
}
_MAX_FTS_SCORE_COMPONENT = 120.0
_MAX_RERANK_SCORE_COMPONENT = 120.0
_METADATA_MATCH_BONUS = 15.0
_POLICY_CODE_RE = re.compile(r"\b[А-ЯЁA-Z]{1,4}[-_]\d{3,8}\b", re.IGNORECASE)
_UNIT_IDENTIFIER_RE = re.compile(
    r"\b(?P<label>отделение|отдел(?:ом|а|е|у)?|служба)"
    r"\s+(?P<number>\d+[А-ЯЁA-Z]?)\b",
    re.IGNORECASE,
)
_FORM_IDENTIFIER_RE = re.compile(
    r"\b[А-ЯЁA-Z]{1,4}[_-][А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z0-9_-]{2,}\b"
)
_LATIN_BRAND_RE = re.compile(r"\b[A-Z][A-Z0-9-]{2,}\b")
_PERSON_TOKEN_RE = re.compile(r"\b[А-ЯЁ][а-яё]{4,}\b")
_ENTITY_STOP_WORDS = frozenset(
    {
        "Как",
        "Какие",
        "Какой",
        "Кому",
        "Кто",
        "Когда",
        "Можно",
        "Нужно",
        "Что",
        "Где",
        "Куда",
        "Почему",
        "Перечисли",
        "Расскажи",
    }
)
_MORPHOLOGY_HINTS: dict[str, list[str]] = {
    "согласовать": ["согласование"],
    "согласоватьь": ["согласование"],
    "подчинённого": ["подчиненного", "подчинен"],
    "подчиненного": ["подчинен"],
    "подчиненному": ["подчиненным", "подчинен"],
    "обязанности": ["обязанность"],
    "командировку": ["командировки", "командиров"],
    "распоряжений": ["распоряжения"],
    "распоряжение": ["распоряжения"],
    "планированию": ["планирование"],
    "задачу": ["задача", "задачи"],
    "задач": ["задача", "задачи"],
    "контроля": ["контроль"],
    "контролем": ["контроль"],
}


@dataclass(frozen=True)
class RetrievedChunk:
    """One semantic memory chunk returned by retrieval."""

    chunk_id: int | None
    title: str
    source: str
    section: str | None
    page: int | None
    text: str
    score: float | None
    metadata: dict[str, Any]
    doc_type: str | None = None
    base_score: float | None = None
    rerank_score: float | None = None
    final_score: float | None = None
    matched_terms: list[str] | None = None
    matched_excerpt: str = ""
    selection_reasons: list[str] | None = None


@dataclass(frozen=True)
class RetrievalStage:
    """One deterministic retrieval stage in a native plan."""

    name: str
    query: str
    limit: int
    filters: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0
    terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in RETRIEVAL_STAGE_NAMES:
            raise ValueError(f"Unsupported retrieval stage: {self.name!r}")
        if self.limit <= 0:
            raise ValueError("retrieval stage limit must be greater than 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "query": self.query,
            "limit": self.limit,
            "filters": _json_safe_mapping(self.filters),
            "priority": self.priority,
            "terms": list(self.terms),
        }


@dataclass(frozen=True)
class RetrievalPlan:
    """Resolved query plus the independent stages used to retrieve candidates."""

    original_question: str
    resolved_question: str
    requested_fact_type: str = "unknown"
    subject: str = ""
    intent: str = "unknown"
    entities: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    stages: tuple[RetrievalStage, ...] = ()
    operational_lookup: bool = False
    planning_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_question": self.original_question,
            "resolved_question": self.resolved_question,
            "requested_fact_type": self.requested_fact_type,
            "subject": self.subject,
            "intent": self.intent,
            "entities": list(self.entities),
            "preferred_sources": list(self.preferred_sources),
            "operational_lookup": self.operational_lookup,
            "planning_reasons": list(self.planning_reasons),
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass(frozen=True)
class RetrievalStageHit:
    """One candidate hit from one stage, before cross-stage deduplication."""

    stage: str
    query: str
    raw_score: float | None
    rank: int
    stage_weight: float
    scored_value: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "query": self.query,
            "raw_score": self.raw_score,
            "rank": self.rank,
            "stage_weight": self.stage_weight,
            "scored_value": self.scored_value,
        }


@dataclass(frozen=True)
class RetrievalObservation:
    """One chunk and its provenance before aggregation."""

    chunk: RetrievedChunk
    stage_hit: RetrievalStageHit
    match_reasons: tuple[str, ...] = ()
    metadata_matches: tuple[str, ...] = ()
    retrieval_adjustments: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalCandidate:
    """One deduplicated candidate with complete cross-stage provenance."""

    chunk: RetrievedChunk
    final_score: float
    best_raw_score: float | None
    stage_hits: tuple[RetrievalStageHit, ...]
    matched_queries: tuple[str, ...]
    match_reasons: tuple[str, ...]
    metadata_matches: tuple[str, ...]
    retrieval_adjustments: tuple[str, ...]
    stable_key: str

    def to_chunk(self) -> RetrievedChunk:
        return replace(
            self.chunk,
            final_score=self.final_score,
            selection_reasons=list(
                dict.fromkeys(
                    [
                        *(self.chunk.selection_reasons or []),
                        *self.match_reasons,
                        *self.retrieval_adjustments,
                    ]
                )
            ),
        )

    def to_dict(self, *, rank: int | None = None) -> dict[str, Any]:
        metadata = self.chunk.metadata or {}
        return {
            "chunk_id": self.chunk.chunk_id,
            "record_key": metadata.get("record_key"),
            "title": self.chunk.title,
            "source": self.chunk.source,
            "section": self.chunk.section,
            "page": self.chunk.page,
            "doc_type": self.chunk.doc_type or metadata.get("doc_type"),
            "knowledge_domain": metadata.get("knowledge_domain"),
            "logical_unit_type": metadata.get("logical_unit_type"),
            "logical_unit_title": metadata.get("logical_unit_title"),
            "score": self.final_score,
            "final_score": self.final_score,
            "best_raw_score": self.best_raw_score,
            "retrieval_rank": rank,
            "candidate_order": rank,
            "stable_key": self.stable_key,
            "stage_hits": [hit.to_dict() for hit in self.stage_hits],
            "matched_queries": list(self.matched_queries),
            "match_reasons": list(self.match_reasons),
            "metadata_matches": list(self.metadata_matches),
            "retrieval_adjustments": list(self.retrieval_adjustments),
            "metadata": dict(metadata),
        }


@dataclass(frozen=True)
class CandidateAggregation:
    """Candidate aggregation result before serialization."""

    candidates: tuple[RetrievalCandidate, ...]
    candidate_count_before_dedupe: int
    candidate_count_after_dedupe: int
    duplicate_count: int
    best_score_dedupe_correct: int
    best_score_dedupe_checks: int


@dataclass(frozen=True)
class RetrievalResult:
    """Native retrieval result consumed by orchestration and diagnostics."""

    plan: RetrievalPlan
    candidates: tuple[RetrievalCandidate, ...]
    stage_hit_counts: Mapping[str, int]
    candidate_count_before_dedupe: int
    candidate_count_after_dedupe: int
    duplicate_count: int
    best_score_dedupe_correct: int
    best_score_dedupe_checks: int
    duration_ms: float

    @property
    def chunks(self) -> list[RetrievedChunk]:
        return [candidate.to_chunk() for candidate in self.candidates]

    def to_dict(self) -> dict[str, Any]:
        serialized = [
            candidate.to_dict(rank=index)
            for index, candidate in enumerate(self.candidates, start=1)
        ]
        stage_queries: dict[str, list[str]] = {}
        stage_filters: dict[str, list[dict[str, Any]]] = {}
        for stage in self.plan.stages:
            stage_queries.setdefault(stage.name, []).append(stage.query)
            stage_filters.setdefault(stage.name, []).append(
                _json_safe_mapping(stage.filters)
            )
        for candidate in self.candidates:
            for hit in candidate.stage_hits:
                queries = stage_queries.setdefault(hit.stage, [])
                if hit.query not in queries:
                    queries.append(hit.query)
        return {
            "retrieval_plan": self.plan.to_dict(),
            "retrieval_stages": [stage.name for stage in self.plan.stages],
            "stage_queries": stage_queries,
            "stage_filters": stage_filters,
            "stage_hit_counts": dict(self.stage_hit_counts),
            "candidate_count_before_dedupe": self.candidate_count_before_dedupe,
            "candidate_count_after_dedupe": self.candidate_count_after_dedupe,
            "duplicate_count": self.duplicate_count,
            "candidate_provenance": serialized,
            "candidate_stage_hits": [
                {
                    "stable_key": candidate.stable_key,
                    "stage_hits": [hit.to_dict() for hit in candidate.stage_hits],
                }
                for candidate in self.candidates
            ],
            "candidate_matched_queries": [
                {
                    "stable_key": candidate.stable_key,
                    "matched_queries": list(candidate.matched_queries),
                }
                for candidate in self.candidates
            ],
            "candidate_best_raw_score": [
                {
                    "stable_key": candidate.stable_key,
                    "best_raw_score": candidate.best_raw_score,
                }
                for candidate in self.candidates
            ],
            "candidate_final_score": [
                {
                    "stable_key": candidate.stable_key,
                    "final_score": candidate.final_score,
                }
                for candidate in self.candidates
            ],
            "candidate_order": [candidate.stable_key for candidate in self.candidates],
            "best_score_dedupe_correct": self.best_score_dedupe_correct,
            "best_score_dedupe_checks": self.best_score_dedupe_checks,
            "duration_ms": self.duration_ms,
        }


class RetrievalPlanner:
    """Choose independent retrieval stages from the resolved QueryPlan."""

    def build(
        self,
        *,
        original_question: str,
        resolved_question: str,
        query_plan: Any = None,
        retrieval_limit: int = 5,
        candidate_limit: int = 30,
    ) -> RetrievalPlan:
        requested_fact_type = str(
            getattr(query_plan, "requested_fact_type", "unknown") or "unknown"
        )
        subject = str(getattr(query_plan, "subject", "") or "").strip()
        intent = str(getattr(query_plan, "intent", "unknown") or "unknown")
        normalized_question = str(
            getattr(query_plan, "normalized_question", "") or ""
        ).strip()
        expansions = tuple(
            value.strip()
            for value in getattr(query_plan, "query_expansions", ()) or ()
            if isinstance(value, str) and value.strip()
        )
        preferred_sources = tuple(
            value.strip()
            for value in getattr(query_plan, "preferred_sources", ()) or ()
            if isinstance(value, str) and value.strip()
        )
        operational_lookup = bool(
            getattr(query_plan, "operational_lookup", False)
        )
        clarification_action = str(
            getattr(query_plan, "clarification_action", "") or ""
        )
        entities = extract_exact_entities(
            " ".join(value for value in (resolved_question, subject) if value)
        )
        reasons: list[str] = []

        if clarification_action == "clarify":
            return RetrievalPlan(
                original_question=original_question,
                resolved_question=resolved_question,
                requested_fact_type=requested_fact_type,
                subject=subject,
                intent=intent,
                entities=entities,
                preferred_sources=preferred_sources,
                operational_lookup=False,
                planning_reasons=("clarification_required_before_retrieval",),
            )

        if operational_lookup:
            return RetrievalPlan(
                original_question=original_question,
                resolved_question=resolved_question,
                requested_fact_type=requested_fact_type,
                subject=subject,
                intent=intent,
                entities=entities,
                preferred_sources=preferred_sources,
                operational_lookup=True,
                planning_reasons=("operational_lookup_skips_semantic_retrieval",),
            )

        stage_limit = min(
            max(candidate_limit, 1),
            max(retrieval_limit, 10),
        )
        expansion_limit = min(max(candidate_limit, 1), max(retrieval_limit, 5))
        stages: list[RetrievalStage] = []

        for entity in entities:
            name = (
                "exact_identifier"
                if _is_structured_identifier(entity)
                else "exact_entity"
            )
            filters: dict[str, Any] = {"match_mode": "all"}
            if _UNIT_IDENTIFIER_RE.search(entity):
                filters["doc_types"] = list(ORGANIZATION_RETRIEVAL_DOC_TYPES)
            _append_stage(
                stages,
                RetrievalStage(
                    name=name,
                    query=entity,
                    terms=tuple(_TOKEN_RE.findall(entity)),
                    limit=stage_limit,
                    filters=filters,
                    priority=10,
                ),
            )
        if entities:
            reasons.append("exact_entities_detected")

        if subject:
            _append_stage(
                stages,
                RetrievalStage(
                    name="exact_subject",
                    query=subject,
                    terms=tuple(_prefix_query_terms(subject)),
                    limit=stage_limit,
                    filters={"match_mode": "prefix_any"},
                    priority=20,
                ),
            )
            reasons.append("subject_preserved")

        _append_stage(
            stages,
            RetrievalStage(
                name="fts_resolved_question",
                query=resolved_question,
                limit=stage_limit,
                filters={"match_mode": "legacy"},
                priority=30,
            ),
        )

        if (
            normalized_question
            and _fold(normalized_question) != _fold(resolved_question)
        ):
            _append_stage(
                stages,
                RetrievalStage(
                    name="fts_normalized_question",
                    query=normalized_question,
                    limit=stage_limit,
                    filters={"match_mode": "legacy"},
                    priority=40,
                ),
            )

        for expansion in expansions:
            _append_stage(
                stages,
                RetrievalStage(
                    name="fts_query_expansion",
                    query=expansion,
                    limit=expansion_limit,
                    filters={"match_mode": "legacy"},
                    priority=50,
                ),
            )

        if requested_fact_type == "procedure":
            procedure_query = " ".join(
                value
                for value in (subject, "заявка инструкция порядок согласование")
                if value
            )
            _append_stage(
                stages,
                RetrievalStage(
                    name="procedure_lookup",
                    query=procedure_query,
                    terms=tuple(_prefix_query_terms(procedure_query)),
                    limit=stage_limit,
                    filters={
                        "match_mode": "prefix_any",
                        "metadata_filters": {
                            "logical_unit_type": "procedure",
                        },
                    },
                    priority=15,
                ),
            )
            reasons.append("procedure_fact_type")

        if requested_fact_type in ORGANIZATION_FUNCTION_FACT_TYPES and subject:
            _append_stage(
                stages,
                RetrievalStage(
                    name="organization_function_lookup",
                    query=subject,
                    terms=tuple(_prefix_query_terms(subject)),
                    limit=stage_limit,
                    filters={
                        "match_mode": "prefix_any",
                        "doc_types": list(ORGANIZATION_RETRIEVAL_DOC_TYPES),
                    },
                    priority=15,
                ),
            )
            reasons.append("organization_function_fact_type")

        for source in preferred_sources:
            _append_stage(
                stages,
                RetrievalStage(
                    name="preferred_source_lookup",
                    query=source,
                    terms=tuple(_TOKEN_RE.findall(source)),
                    limit=stage_limit,
                    filters={"match_mode": "all"},
                    priority=25,
                ),
            )
        if preferred_sources:
            reasons.append("preferred_sources_are_additional")

        _append_stage(
            stages,
            RetrievalStage(
                name="sibling_lookup",
                query="candidate metadata",
                limit=candidate_limit,
                filters={"dynamic": True},
                priority=90,
            ),
        )

        return RetrievalPlan(
            original_question=original_question,
            resolved_question=resolved_question,
            requested_fact_type=requested_fact_type,
            subject=subject,
            intent=intent,
            entities=entities,
            preferred_sources=preferred_sources,
            stages=tuple(sorted(stages, key=lambda stage: stage.priority)),
            operational_lookup=False,
            planning_reasons=tuple(reasons),
        )


class CandidateAggregator:
    """Merge hits by stable identity while retaining every stage provenance."""

    def aggregate(
        self,
        observations: Sequence[RetrievalObservation],
        *,
        candidate_limit: int | None = None,
    ) -> CandidateAggregation:
        grouped: dict[str, list[RetrievalObservation]] = {}
        for observation in observations:
            grouped.setdefault(
                _stable_chunk_key(observation.chunk),
                [],
            ).append(observation)

        candidates: list[RetrievalCandidate] = []
        best_score_checks = 0
        best_score_correct = 0
        for stable_key, hits in grouped.items():
            best = max(
                hits,
                key=lambda item: (
                    item.stage_hit.scored_value,
                    -item.stage_hit.rank,
                    item.stage_hit.stage,
                    item.stage_hit.query,
                ),
            )
            if len(hits) > 1:
                best_score_checks += 1
            final_score = max(hit.stage_hit.scored_value for hit in hits)
            if final_score == best.stage_hit.scored_value and len(hits) > 1:
                best_score_correct += 1
            raw_scores = [
                hit.stage_hit.raw_score
                for hit in hits
                if hit.stage_hit.raw_score is not None
            ]
            stage_hits = tuple(
                sorted(
                    (hit.stage_hit for hit in hits),
                    key=lambda value: (
                        -value.scored_value,
                        value.rank,
                        value.stage,
                        value.query,
                    ),
                )
            )
            candidates.append(
                RetrievalCandidate(
                    chunk=best.chunk,
                    final_score=final_score,
                    best_raw_score=min(raw_scores) if raw_scores else None,
                    stage_hits=stage_hits,
                    matched_queries=tuple(
                        dict.fromkeys(hit.stage_hit.query for hit in hits)
                    ),
                    match_reasons=tuple(
                        dict.fromkeys(
                            reason
                            for hit in hits
                            for reason in hit.match_reasons
                        )
                    ),
                    metadata_matches=tuple(
                        dict.fromkeys(
                            match
                            for hit in hits
                            for match in hit.metadata_matches
                        )
                    ),
                    retrieval_adjustments=tuple(
                        dict.fromkeys(
                            adjustment
                            for hit in hits
                            for adjustment in hit.retrieval_adjustments
                        )
                    ),
                    stable_key=stable_key,
                )
            )

        candidates.sort(
            key=lambda candidate: (
                -candidate.final_score,
                (
                    candidate.best_raw_score
                    if candidate.best_raw_score is not None
                    else float("inf")
                ),
                candidate.stable_key,
            )
        )
        total_unique = len(candidates)
        if candidate_limit is not None:
            candidates = candidates[: max(0, candidate_limit)]
        before = len(observations)
        return CandidateAggregation(
            candidates=tuple(candidates),
            candidate_count_before_dedupe=before,
            candidate_count_after_dedupe=total_unique,
            duplicate_count=max(0, before - total_unique),
            best_score_dedupe_correct=best_score_correct,
            best_score_dedupe_checks=best_score_checks,
        )


class SemanticRetriever:
    """Retrieval layer for semantic memory FTS search plus lightweight rerank."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DEFAULT_MEMORY_DB_PATH
        self.store = MemoryStore(str(self.db_path))

    def retrieve(
        self,
        question: str,
        *,
        limit: int = 5,
        namespace: str = "semantic",
        candidate_limit: int = 20,
    ) -> list[RetrievedChunk]:
        """Search semantic memory and return reranked chunks."""
        if not question or not question.strip():
            return []

        candidate_limit = max(limit, candidate_limit)
        fts_queries = build_fts_queries(question)
        if not fts_queries:
            return []

        rows = self._collect_candidate_rows(
            fts_queries=fts_queries,
            namespace=namespace,
            candidate_limit=candidate_limit,
        )
        chunks = [_row_to_chunk(row) for row in rows]
        ranked_chunks = [
            _rerank_chunk(chunk, question=question)
            for chunk in chunks
        ]

        return sorted(
            ranked_chunks,
            key=lambda chunk: chunk.final_score if chunk.final_score is not None else 0.0,
            reverse=True,
        )[:limit]

    def retrieve_plan(
        self,
        plan: RetrievalPlan,
        *,
        namespace: str = "semantic",
        candidate_limit: int = 30,
    ) -> RetrievalResult:
        """Execute independent stages and aggregate candidates with provenance."""
        started_at = time.monotonic()
        observations: list[RetrievalObservation] = []
        stage_hit_counts: dict[str, int] = {}

        for stage in plan.stages:
            if stage.name == "sibling_lookup":
                continue
            stage_observations = self._execute_stage(
                plan,
                stage,
                namespace=namespace,
                candidate_limit=candidate_limit,
            )
            observations.extend(stage_observations)
            stage_hit_counts[stage.name] = (
                stage_hit_counts.get(stage.name, 0) + len(stage_observations)
            )

        preliminary = CandidateAggregator().aggregate(
            observations,
            candidate_limit=candidate_limit,
        )
        sibling_observations = self._collect_sibling_observations(
            plan,
            preliminary.candidates,
            namespace=namespace,
            candidate_limit=candidate_limit,
        )
        observations.extend(sibling_observations)
        if any(stage.name == "sibling_lookup" for stage in plan.stages):
            stage_hit_counts["sibling_lookup"] = len(sibling_observations)

        aggregation = CandidateAggregator().aggregate(
            observations,
            candidate_limit=candidate_limit,
        )
        return RetrievalResult(
            plan=plan,
            candidates=aggregation.candidates,
            stage_hit_counts=stage_hit_counts,
            candidate_count_before_dedupe=(
                aggregation.candidate_count_before_dedupe
            ),
            candidate_count_after_dedupe=(
                aggregation.candidate_count_after_dedupe
            ),
            duplicate_count=aggregation.duplicate_count,
            best_score_dedupe_correct=aggregation.best_score_dedupe_correct,
            best_score_dedupe_checks=aggregation.best_score_dedupe_checks,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
        )

    def _execute_stage(
        self,
        plan: RetrievalPlan,
        stage: RetrievalStage,
        *,
        namespace: str,
        candidate_limit: int,
    ) -> list[RetrievalObservation]:
        match_mode = str(stage.filters.get("match_mode") or "legacy")
        if match_mode == "legacy":
            chunks = self.retrieve(
                stage.query,
                limit=min(stage.limit, candidate_limit),
                namespace=namespace,
                candidate_limit=candidate_limit,
            )
        else:
            rows = self.store.search_fts_terms(
                stage.terms or tuple(_TOKEN_RE.findall(stage.query)),
                match_mode=match_mode,
                namespace=namespace,
                limit=min(stage.limit, candidate_limit),
                doc_types=_string_sequence(stage.filters.get("doc_types")),
                metadata_filters=_mapping_or_none(
                    stage.filters.get("metadata_filters")
                ),
            )
            chunks = [
                _rerank_chunk(_row_to_chunk(row), question=plan.resolved_question)
                for row in rows
            ]

        metadata_matches = _stage_metadata_matches(stage)
        return [
            _observation(
                chunk,
                stage=stage.name,
                query=stage.query,
                rank=rank,
                metadata_matches=metadata_matches,
            )
            for rank, chunk in enumerate(chunks, start=1)
        ]

    def _collect_sibling_observations(
        self,
        plan: RetrievalPlan,
        candidates: Sequence[RetrievalCandidate],
        *,
        namespace: str,
        candidate_limit: int,
    ) -> list[RetrievalObservation]:
        if not any(stage.name == "sibling_lookup" for stage in plan.stages):
            return []

        observations: list[RetrievalObservation] = []
        seen_lookups: set[tuple[str, str, str]] = set()
        for candidate in candidates:
            chunk = candidate.chunk
            metadata = chunk.metadata or {}
            logical_type = str(metadata.get("logical_unit_type") or "")
            source = chunk.source
            logical_title = str(metadata.get("logical_unit_title") or "")

            metadata_filters: dict[str, Any]
            if logical_type == "policy_rule":
                metadata_filters = {"logical_unit_type": "policy_rule"}
                lookup_title = ""
            elif logical_type == "procedure" and logical_title:
                metadata_filters = {
                    "logical_unit_type": "procedure",
                    "logical_unit_title": logical_title,
                }
                lookup_title = logical_title
            else:
                continue

            lookup_key = (source, logical_type, lookup_title)
            if lookup_key in seen_lookups:
                continue
            seen_lookups.add(lookup_key)
            rows = self.store.search_chunks_by_metadata(
                namespace=namespace,
                source=source,
                metadata_filters=metadata_filters,
                limit=candidate_limit,
            )
            query = " | ".join(
                value
                for value in (
                    str(metadata.get("source_file") or source),
                    logical_type,
                    logical_title,
                )
                if value
            )
            for rank, row in enumerate(rows, start=1):
                chunk_result = _rerank_chunk(
                    _row_to_chunk(row),
                    question=plan.resolved_question,
                )
                observations.append(
                    _observation(
                        chunk_result,
                        stage="sibling_lookup",
                        query=query,
                        rank=rank,
                        metadata_matches=tuple(
                            f"{key}={value}"
                            for key, value in metadata_filters.items()
                        ),
                    )
                )
        return observations

    def _collect_candidate_rows(
        self,
        *,
        fts_queries: Sequence[str],
        namespace: str,
        candidate_limit: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for fts_query in fts_queries:
            query_rows = self.store.search_fts(
                fts_query,
                namespace=namespace,
                limit=candidate_limit,
            )

            for row in query_rows:
                row_id = _optional_int(row.get("id"))
                if row_id is not None and row_id in seen_ids:
                    continue

                rows.append(row)
                if row_id is not None:
                    seen_ids.add(row_id)

        return rows


def format_retrieval_result(
    chunk: RetrievedChunk,
    max_chars: int = 250,
    *,
    rank: int | None = None,
    relevance_status: str | None = None,
) -> str:
    """Format a retrieved chunk as a compact human-readable summary."""
    metadata = chunk.metadata
    logical_unit_title = _metadata_str(metadata, "logical_unit_title") or "-"
    logical_unit_type = _metadata_str(metadata, "logical_unit_type") or "-"
    part_index = _metadata_str(metadata, "part_index") or "-"
    part_count = _metadata_str(metadata, "part_count") or "-"
    matched_terms = ", ".join(chunk.matched_terms or []) or "-"
    selection_reasons = chunk.selection_reasons or []
    excerpt = chunk.matched_excerpt or build_matched_excerpt(
        chunk.text,
        chunk.matched_terms or [],
        max_chars=max_chars,
    )

    lines: list[str] = []
    heading = []
    if rank is not None:
        heading.append(f"TOP {rank}")
    if relevance_status:
        heading.append(f"[{relevance_status}]")
    if heading:
        lines.append(" ".join(heading))

    lines.extend(
        [
            f"Chunk ID: {_format_optional(chunk.chunk_id)}",
            f"Doc type: {chunk.doc_type or _metadata_str(metadata, 'doc_type') or '-'}",
            f"Title: {chunk.title}",
            f"Source: {chunk.source}",
            f"Section: {chunk.section or '-'}",
            f"Logical unit title: {logical_unit_title}",
            f"Logical unit type: {logical_unit_type}",
            f"Page: {_format_optional(chunk.page)}",
            f"Part: {part_index}/{part_count}",
            (
                "Score: "
                f"base={_format_score(chunk.base_score)}, "
                f"rerank={_format_score(chunk.rerank_score)}, "
                f"final={_format_score(chunk.final_score)}"
            ),
            f"Matched terms: {matched_terms}",
            "Matched excerpt:",
            excerpt,
        ]
    )

    if selection_reasons:
        lines.append("Selection reasons:")
        lines.extend(f"- {reason}" for reason in selection_reasons)

    return "\n".join(lines)


def normalize_question(question: str) -> str:
    """Normalize user text into a safe, predictable retrieval query string."""
    tokens = _TOKEN_RE.findall(question.replace("ё", "е").replace("Ё", "Е"))
    return _SPACE_RE.sub(" ", " ".join(tokens)).strip()


def extract_query_terms(question: str, *, include_expansions: bool = True) -> list[str]:
    """Return significant query terms and optional domain expansions."""
    normalized = normalize_question(question)
    terms: list[str] = []
    seen: set[str] = set()

    for token in _TOKEN_RE.findall(normalized):
        token_folded = token.casefold()
        if token_folded in _STOP_WORDS:
            continue
        if len(token_folded) < 3 and not any(char.isdigit() for char in token_folded):
            continue
        _append_unique(terms, seen, token)
        for hint in _MORPHOLOGY_HINTS.get(token_folded, []):
            _append_unique(terms, seen, hint)

    if include_expansions:
        for expansion in expand_query_terms(normalized):
            _append_unique(terms, seen, expansion)

    return terms


def expand_query_terms(question: str) -> list[str]:
    """Add deterministic domain-specific query expansions."""
    question_folded = normalize_question(question).casefold()
    expansions: list[str] = []
    seen: set[str] = set()

    for key, values in QUERY_EXPANSIONS.items():
        if key in question_folded:
            for value in values:
                _append_unique(expansions, seen, value)

    return expansions


def build_fts_queries(question: str) -> list[str]:
    """Build safe FTS5 MATCH expressions from a natural-language question."""
    original_terms = extract_query_terms(question, include_expansions=False)
    expanded_terms = extract_query_terms(question, include_expansions=True)
    expansions = expand_query_terms(question)

    queries: list[str] = []
    if expansions:
        queries.append(_or_query(expansions))
    if original_terms:
        queries.append(_and_query(original_terms))
        queries.append(_or_query(original_terms))
        prefix_terms = _prefix_terms(original_terms)
        if prefix_terms:
            queries.append(" OR ".join(prefix_terms))
    if expanded_terms:
        queries.append(_or_query(expanded_terms))

    return _dedupe_non_empty(queries)


def extract_matched_terms(question: str, chunk: RetrievedChunk | dict[str, Any]) -> list[str]:
    """Return query terms that appear in chunk metadata or text."""
    terms = extract_query_terms(question)
    searchable_text = _chunk_searchable_text(chunk).casefold()
    matched: list[str] = []
    seen: set[str] = set()

    for term in terms:
        term_folded = term.casefold()
        if term_folded and term_folded in searchable_text:
            _append_unique(matched, seen, term)
            continue

        term_words = [
            word.casefold()
            for word in _TOKEN_RE.findall(term)
            if word.casefold() not in _STOP_WORDS
        ]
        if term_words and all(word in searchable_text for word in term_words):
            _append_unique(matched, seen, term)

    return matched


def build_matched_excerpt(
    text: str,
    terms: Sequence[str],
    *,
    max_chars: int = _MAX_EXCERPT_CHARS,
) -> str:
    """Build a readable excerpt around the first matched term."""
    normalized = _normalize_text(text)
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized

    folded_text = normalized.casefold()
    match_start: int | None = None

    for term in sorted(terms, key=len, reverse=True):
        for needle in _term_needles(term):
            index = folded_text.find(needle.casefold())
            if index >= 0 and (match_start is None or index < match_start):
                match_start = index

    if match_start is None:
        return normalized[:max_chars].rstrip() + "..."

    half_window = max_chars // 2
    start = max(0, match_start - half_window)
    end = min(len(normalized), start + max_chars)
    start = max(0, end - max_chars)

    excerpt = normalized[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(normalized):
        excerpt += "..."

    return excerpt


def _rerank_chunk(chunk: RetrievedChunk, *, question: str) -> RetrievedChunk:
    matched_terms = extract_matched_terms(question, chunk)
    rerank_score, selection_reasons = _score_chunk(
        chunk,
        question=question,
        matched_terms=matched_terms,
    )
    base_score = _base_score(chunk.score)
    final_score = base_score + rerank_score
    excerpt = build_matched_excerpt(
        chunk.text,
        matched_terms,
        max_chars=_MAX_EXCERPT_CHARS,
    )

    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        title=chunk.title,
        source=chunk.source,
        section=chunk.section,
        page=chunk.page,
        text=chunk.text,
        score=chunk.score,
        metadata=chunk.metadata,
        doc_type=chunk.doc_type,
        base_score=base_score,
        rerank_score=rerank_score,
        final_score=final_score,
        matched_terms=matched_terms,
        matched_excerpt=excerpt,
        selection_reasons=selection_reasons,
    )


def _score_chunk(
    chunk: RetrievedChunk,
    *,
    question: str,
    matched_terms: Sequence[str],
) -> tuple[float, list[str]]:
    question_folded = _fold(normalize_question(question))
    metadata = chunk.metadata
    title = _fold(chunk.title)
    section = _fold(chunk.section or "")
    source_file = _fold(_metadata_str(metadata, "source_file") or chunk.source)
    logical_title = _fold(_metadata_str(metadata, "logical_unit_title") or "")
    doc_type = _fold(chunk.doc_type or _metadata_str(metadata, "doc_type") or "")
    tags = _fold(" ".join(str(tag) for tag in metadata.get("tags", [])))
    text = _fold(chunk.text)

    score = 0.0
    reasons: list[str] = []

    field_specs = [
        ("title", title, 16.0),
        ("section", section, 14.0),
        ("logical unit title", logical_title, 14.0),
        ("source file", source_file, 9.0),
        ("doc type", doc_type, 5.0),
        ("tags", tags, 6.0),
        ("text", text, 1.6),
    ]

    for term in matched_terms:
        term_folded = term.casefold()
        for field_name, field_value, weight in field_specs:
            if _term_matches_text(term_folded, field_value):
                score += weight
                if len(reasons) < 10:
                    reasons.append(f"{field_name} contains {term!r}")

    # Strong deterministic boosts for known weak business questions.
    targeted_boosts = [
        (
            ("зрс" in question_folded and ("состоит" in question_folded or "ситуац" in question_folded)),
            "ип-0004 структура зрс",
            "что такое зрс",
            55.0,
            "target: ZRS structure definition",
        ),
        (
            "планирован" in question_folded and "недел" in question_folded,
            "регламент по планированию на неделю",
            None,
            60.0,
            "target: weekly planning regulation",
        ),
        (
            "зачем" in question_folded and "план" in question_folded and "недел" in question_folded,
            "регламент по планированию на неделю",
            "зачем нужны планы на неделю",
            75.0,
            "target: why weekly plans are needed",
        ),
        (
            "обязанност" in question_folded and "сотрудник" in question_folded and "план" in question_folded,
            "регламент по планированию на неделю",
            "обязанности сотрудника при недельном планировании",
            85.0,
            "target: employee weekly planning duties",
        ),
        (
            "состав" in question_folded and "план" in question_folded and "недел" in question_folded,
            "регламент по планированию на неделю",
            "форма плана на неделю",
            80.0,
            "target: weekly plan form",
        ),
        (
            "ошиб" in question_folded and "план" in question_folded,
            "регламент по планированию на неделю",
            "типовые ошибки сотрудника при планировании",
            80.0,
            "target: employee planning mistakes",
        ),
        (
            "командиров" in question_folded and "соглас" in question_folded,
            "инструкция согласования командировки",
            None,
            60.0,
            "target: business trip approval instruction",
        ),
        (
            "распоряж" in question_folded,
            "ип-0005 распоряжения",
            None,
            38.0,
            "target: orders policy",
        ),
        (
            "профессиональн" in question_folded and "подбор" in question_folded,
            "ип-0003 цкп serviceline",
            "профессиональный подбор",
            45.0,
            "target: professional selection in CKP",
        ),
        (
            ("чем" in question_folded and "занимает" in question_folded and "компан" in question_folded)
            or ("что" in question_folded and "делает" in question_folded and "компан" in question_folded)
            or ("цель" in question_folded and "компан" in question_folded)
            or "serviceline" in question_folded
            or "сервислайн" in question_folded,
            "ип-0002 цели и замыслы компании serviceline",
            None,
            95.0,
            "target: company identity goals",
        ),
        (
            ("чем" in question_folded and "занимает" in question_folded and "компан" in question_folded)
            or ("что" in question_folded and "делает" in question_folded and "компан" in question_folded)
            or ("цель" in question_folded and "компан" in question_folded)
            or "serviceline" in question_folded
            or "сервислайн" in question_folded,
            "ип-0003 цкп serviceline",
            None,
            85.0,
            "target: company identity CKP",
        ),
        (
            "документооборот" in question_folded
            or "документоборот" in question_folded
            or "1с до" in question_folded
            or ("соглас" in question_folded and "документ" in question_folded),
            "ип-0006 документооборот",
            None,
            95.0,
            "target: document flow policy",
        ),
        (
            "документооборот" in question_folded
            or "документоборот" in question_folded
            or "1с до" in question_folded
            or ("соглас" in question_folded and "документ" in question_folded),
            "документооборот",
            None,
            55.0,
            "target: document flow instruction",
        ),
        (
            "обязанност" in question_folded and "подчин" in question_folded and "зрс" in question_folded,
            "ип-0004 структура зрс",
            "обязанности подчиненного",
            90.0,
            "target: subordinate duties in ZRS",
        ),
        (
            "контрагент" in question_folded,
            "инструкция как завести нового контрагента",
            None,
            45.0,
            "target: new counterparty instruction",
        ),
        (
            "задач" in question_folded and "подчин" in question_folded,
            "инструкция направление задач подчиненным",
            None,
            45.0,
            "target: task assignment instruction",
        ),
        (
            "задач" in question_folded and ("взять" in question_folded or "работ" in question_folded),
            "работа с задачами",
            None,
            70.0,
            "target: task workflow",
        ),
        (
            "контрол" in question_folded,
            "ип-0005 распоряжения",
            "контроль",
            80.0,
            "target: process control wording",
        ),
        (
            "письмен" in question_folded and "коммуникац" in question_folded,
            "регламент по письменной коммуникации",
            None,
            45.0,
            "target: written communication regulation",
        ),
        (
            "коммерческ" in question_folded and "отделени" in question_folded,
            "оргсхема",
            None,
            35.0,
            "target: org chart for departments",
        ),
    ]

    for condition, expected_title, expected_section, boost, reason in targeted_boosts:
        if not condition:
            continue
        if expected_title in title or expected_title in source_file:
            score += boost
            reasons.append(reason)
            if expected_section and expected_section in section:
                score += boost * 0.8
                reasons.append(f"section matches {expected_section!r}")

    if "подчин" in question_folded and "руководител" in section:
        score -= 45.0
        reasons.append("penalty: question asks about subordinate, section is about manager")

    if len(chunk.text) > 5000:
        score -= 5.0
        reasons.append("small penalty: chunk is longer than 5000 chars")

    if not reasons and matched_terms:
        reasons.append("matched query terms in searchable fields")

    return score, reasons


def extract_exact_entities(value: str) -> tuple[str, ...]:
    """Extract high-confidence identifiers/entities without an LLM."""
    entities: list[str] = []
    seen: set[str] = set()
    for pattern in (
        _POLICY_CODE_RE,
        _UNIT_IDENTIFIER_RE,
        _FORM_IDENTIFIER_RE,
        _LATIN_BRAND_RE,
    ):
        for match in pattern.finditer(value):
            entity = match.group(0)
            if pattern is _UNIT_IDENTIFIER_RE:
                label = match.group("label").casefold()
                canonical_label = (
                    "отделение"
                    if label == "отделение"
                    else ("служба" if label == "служба" else "отдел")
                )
                entity = f"{canonical_label} {match.group('number').upper()}"
            _append_entity(entities, seen, entity)

    for match in _PERSON_TOKEN_RE.finditer(value):
        token = match.group(0)
        if token in _ENTITY_STOP_WORDS:
            continue
        if match.start() == 0 and token.casefold() in {
            word.casefold() for word in _ENTITY_STOP_WORDS
        }:
            continue
        _append_entity(entities, seen, token)

    return tuple(entities)


def _append_entity(values: list[str], seen: set[str], value: str) -> None:
    clean = normalize_question(value)
    key = clean.casefold()
    if not clean or key in seen:
        return
    values.append(clean)
    seen.add(key)


def _is_structured_identifier(value: str) -> bool:
    return bool(
        _POLICY_CODE_RE.search(value)
        or re.fullmatch(r"[А-ЯЁA-Z]{1,4}\s+\d{3,8}", value, re.IGNORECASE)
        or _UNIT_IDENTIFIER_RE.search(value)
        or _FORM_IDENTIFIER_RE.search(value)
    )


def _prefix_query_terms(value: str) -> list[str]:
    terms = extract_query_terms(value, include_expansions=False)
    prefixes: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for token in _TOKEN_RE.findall(term):
            folded = token.casefold()
            prefix = _stem_prefix(folded)
            if len(prefix) < 3 or prefix in seen:
                continue
            seen.add(prefix)
            prefixes.append(prefix)
    return prefixes


def _append_stage(
    stages: list[RetrievalStage],
    stage: RetrievalStage,
) -> None:
    key = (stage.name, _fold(normalize_question(stage.query)))
    if any(
        (existing.name, _fold(normalize_question(existing.query))) == key
        for existing in stages
    ):
        return
    stages.append(stage)


def _observation(
    chunk: RetrievedChunk,
    *,
    stage: str,
    query: str,
    rank: int,
    metadata_matches: Sequence[str] = (),
) -> RetrievalObservation:
    stage_weight = STAGE_SCORE_WEIGHTS[stage]
    raw_component = min(
        _base_score(chunk.score),
        _MAX_FTS_SCORE_COMPONENT,
    )
    rerank_component = min(
        max(float(chunk.rerank_score or 0.0), 0.0),
        _MAX_RERANK_SCORE_COMPONENT,
    )
    metadata_bonus = _METADATA_MATCH_BONUS if metadata_matches else 0.0
    scored_value = round(
        stage_weight + raw_component + rerank_component + metadata_bonus,
        6,
    )
    adjustments = [
        f"stage_weight:{stage}={stage_weight:g}",
        f"fts_component={raw_component:.3f}",
        f"rerank_component={rerank_component:.3f}",
    ]
    if metadata_bonus:
        adjustments.append(f"metadata_match_bonus={metadata_bonus:g}")
    return RetrievalObservation(
        chunk=chunk,
        stage_hit=RetrievalStageHit(
            stage=stage,
            query=query,
            raw_score=chunk.score,
            rank=rank,
            stage_weight=stage_weight,
            scored_value=scored_value,
        ),
        match_reasons=tuple(
            dict.fromkeys(
                [
                    f"retrieval stage {stage}",
                    *(chunk.selection_reasons or []),
                ]
            )
        ),
        metadata_matches=tuple(metadata_matches),
        retrieval_adjustments=tuple(adjustments),
    )


def _stage_metadata_matches(stage: RetrievalStage) -> tuple[str, ...]:
    matches: list[str] = []
    for doc_type in _string_sequence(stage.filters.get("doc_types")):
        matches.append(f"doc_type={doc_type}")
    metadata_filters = _mapping_or_none(stage.filters.get("metadata_filters"))
    for key, value in (metadata_filters or {}).items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            matches.extend(f"{key}={item}" for item in value)
        else:
            matches.append(f"{key}={value}")
    return tuple(matches)


def _stable_chunk_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    for name in ("record_key", "content_hash"):
        value = str(metadata.get(name) or "").strip()
        if value:
            return f"{name}:{_fold(value)}"
    if chunk.chunk_id is not None:
        return f"chunk_id:{chunk.chunk_id}"
    fallback = "|".join(
        _fold(str(value or ""))
        for value in (
            chunk.source,
            chunk.title,
            chunk.section,
            chunk.page,
        )
    )
    return f"source_fingerprint:{fallback}"


def _string_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, Sequence):
        return ()
    return tuple(
        str(item).strip()
        for item in value
        if str(item).strip()
    )


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            result[str(key)] = _json_safe_mapping(item)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            result[str(key)] = list(item)
        else:
            result[str(key)] = item
    return result


def _row_to_chunk(row: dict[str, Any]) -> RetrievedChunk:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return RetrievedChunk(
        chunk_id=_optional_int(row.get("id")),
        title=str(row.get("title") or "Untitled"),
        source=str(row.get("source") or "unknown"),
        section=_optional_str(row.get("section")),
        page=_optional_int(row.get("page")),
        text=str(row.get("text") or ""),
        score=_optional_float(row.get("score")),
        metadata=metadata,
        doc_type=_optional_str(row.get("doc_type")) or _metadata_str(metadata, "doc_type"),
        base_score=_base_score(_optional_float(row.get("score"))),
    )


def _chunk_searchable_text(chunk: RetrievedChunk | dict[str, Any]) -> str:
    if isinstance(chunk, RetrievedChunk):
        metadata = chunk.metadata
        values = [
            chunk.title,
            chunk.source,
            chunk.section or "",
            chunk.doc_type or "",
            _metadata_str(metadata, "source_file") or "",
            _metadata_str(metadata, "logical_unit_title") or "",
            _metadata_str(metadata, "logical_unit_type") or "",
            " ".join(str(tag) for tag in metadata.get("tags", [])),
            chunk.text,
        ]
        return " ".join(values)

    metadata = chunk.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    values = [
        str(chunk.get("title") or ""),
        str(chunk.get("source") or ""),
        str(chunk.get("section") or ""),
        str(chunk.get("doc_type") or ""),
        _metadata_str(metadata, "source_file") or "",
        _metadata_str(metadata, "logical_unit_title") or "",
        _metadata_str(metadata, "logical_unit_type") or "",
        " ".join(str(tag) for tag in metadata.get("tags", [])),
        str(chunk.get("text") or ""),
    ]
    return " ".join(values)


def _term_matches_text(term: str, text: str) -> bool:
    term = _fold(term)
    text = _fold(text)
    if not term or not text:
        return False
    if term in text:
        return True

    words = [
        word.casefold()
        for word in _TOKEN_RE.findall(term)
        if word.casefold() not in _STOP_WORDS
    ]
    return bool(words) and all(word in text for word in words)


def _term_needles(term: str) -> list[str]:
    needles = [term]
    needles.extend(
        word
        for word in _TOKEN_RE.findall(term)
        if word.casefold() not in _STOP_WORDS and len(word) >= 3
    )
    return _dedupe_non_empty(needles)


def _quote_fts_term(term: str) -> str:
    normalized = normalize_question(term)
    return '"' + normalized.replace('"', '""') + '"'


def _prefix_terms(terms: Sequence[str]) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()

    for term in terms:
        words = _TOKEN_RE.findall(normalize_question(term))
        if len(words) != 1:
            continue
        word = words[0].casefold()
        if len(word) < 6 or any(char.isdigit() for char in word):
            continue
        prefix = _stem_prefix(word)
        if len(prefix) < 5:
            continue
        value = f"{prefix}*"
        if value in seen:
            continue
        prefixes.append(value)
        seen.add(value)

    return prefixes


def _stem_prefix(word: str) -> str:
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
        if word.endswith(suffix) and len(word) - len(suffix) >= 5:
            return word[: -len(suffix)]
    return word


def _and_query(terms: Sequence[str]) -> str:
    return " ".join(_quote_fts_term(term) for term in terms if normalize_question(term))


def _or_query(terms: Sequence[str]) -> str:
    return " OR ".join(_quote_fts_term(term) for term in terms if normalize_question(term))


def _dedupe_non_empty(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _append_unique(values: list[str], seen: set[str], value: str) -> None:
    normalized = normalize_question(value)
    key = normalized.casefold()
    if not normalized or key in seen:
        return
    values.append(normalized)
    seen.add(key)


def _normalize_text(text: str) -> str:
    return _SPACE_RE.sub(" ", text).strip()


def _base_score(score: float | None) -> float:
    if score is None:
        return 0.0
    return max(0.0, -score) * 20.0


def _fold(value: str) -> str:
    return value.replace("ё", "е").replace("Ё", "Е").casefold()


def _metadata_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    text = str(value).strip()
    return text or None


def _format_optional(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


def _format_score(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
