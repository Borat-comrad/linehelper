"""Coverage-aware context planning and deterministic prompt composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from linehelper.rag.retriever import RetrievedChunk

if TYPE_CHECKING:
    from linehelper.rag.query_analyzer import QueryPlan


ANSWER_SHAPES = frozenset(
    {
        "single_fact",
        "procedure",
        "list",
        "responsibility",
        "definition",
        "comparison",
        "default",
    }
)
RESPONSIBILITY_FACT_TYPES = frozenset(
    {
        "responsible_person",
        "primary_contact",
        "unit_head",
        "document_recipient",
    }
)
ABSOLUTE_CONTEXT_MAX_CHUNKS = 6
DEFAULT_CONTEXT_MAX_CHARS = 8_000
_LONG_CHUNK_THRESHOLD = 2_800
_LONG_CHUNK_EXCERPT_CHARS = 700


@dataclass(frozen=True)
class ContextRequirement:
    """One compact coverage requirement for the prompt context."""

    key: str
    required: bool = True
    max_items: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "required": self.required,
            "max_items": self.max_items,
        }


@dataclass(frozen=True)
class ContextBudget:
    """Absolute deterministic limits applied after required coverage."""

    max_chunks: int
    max_chars: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_chunks": self.max_chunks,
            "max_chars": self.max_chars,
        }


@dataclass(frozen=True)
class ContextPlan:
    """Requested answer shape, coverage requirements and bounded budget."""

    requested_fact_type: str
    answer_shape: str
    requirements: tuple[ContextRequirement, ...]
    budget: ContextBudget
    score_ratio: float

    def __post_init__(self) -> None:
        if self.answer_shape not in ANSWER_SHAPES:
            raise ValueError(f"Unsupported answer shape: {self.answer_shape!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_fact_type": self.requested_fact_type,
            "answer_shape": self.answer_shape,
            "requirements": [
                requirement.to_dict() for requirement in self.requirements
            ],
            "budget": self.budget.to_dict(),
            "score_ratio": self.score_ratio,
        }


@dataclass(frozen=True)
class ContextSelection:
    """Selected chunks plus native coverage and exclusion diagnostics."""

    plan: ContextPlan
    selected: tuple[RetrievedChunk, ...]
    selected_reasons: Mapping[str, tuple[str, ...]]
    excluded_reasons: Mapping[str, str]
    coverage_required: Mapping[str, int]
    coverage_satisfied: Mapping[str, bool]
    estimated_chars: int

    def to_dict(
        self,
        *,
        applied_chunks: Sequence[RetrievedChunk] | None = None,
    ) -> dict[str, Any]:
        applied = tuple(self.selected if applied_chunks is None else applied_chunks)
        applied_keys = {_stable_chunk_key(chunk) for chunk in applied}
        return {
            "context_plan": self.plan.to_dict(),
            "context_requirements": [
                requirement.to_dict() for requirement in self.plan.requirements
            ],
            "context_budget": self.plan.budget.to_dict(),
            "selected_context": [
                _chunk_diagnostic(
                    chunk,
                    reasons=self.selected_reasons.get(
                        _stable_chunk_key(chunk),
                        ("supporting_context",),
                    ),
                )
                for chunk in applied
            ],
            "selected_context_reasons": [
                {
                    "stable_key": _stable_chunk_key(chunk),
                    "reasons": list(
                        self.selected_reasons.get(
                            _stable_chunk_key(chunk),
                            ("supporting_context",),
                        )
                    ),
                }
                for chunk in applied
            ],
            "excluded_candidate_reasons": [
                {"stable_key": key, "reason": reason}
                for key, reason in self.excluded_reasons.items()
            ],
            "coverage_required": dict(self.coverage_required),
            "coverage_satisfied": dict(self.coverage_satisfied),
            "context_size": {
                "chunks": len(applied),
                "estimated_chars": sum(
                    _estimated_context_chars(chunk) for chunk in applied
                ),
                "planned_chunks": len(self.selected),
                "planned_estimated_chars": self.estimated_chars,
                "max_chunks": self.plan.budget.max_chunks,
                "max_chars": self.plan.budget.max_chars,
            },
            "context_applied": applied_keys
            == {_stable_chunk_key(chunk) for chunk in self.selected},
        }


class ContextPlanner:
    """Build the smallest useful coverage plan from QueryPlan v2."""

    def build(
        self,
        *,
        query_plan: QueryPlan | None,
        configured_max_chunks: int,
        max_context_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
        score_ratio: float,
    ) -> ContextPlan:
        requested_fact_type = (
            str(getattr(query_plan, "requested_fact_type", "unknown") or "unknown")
            .strip()
            .lower()
        )
        answer_shape = _answer_shape(requested_fact_type)
        configured_limit = max(1, int(configured_max_chunks))
        max_chunks = min(ABSOLUTE_CONTEXT_MAX_CHUNKS, configured_limit)
        requirements: tuple[ContextRequirement, ...]

        if answer_shape == "list":
            max_chunks = min(
                ABSOLUTE_CONTEXT_MAX_CHUNKS,
                max(configured_limit, 4),
            )
            requirements = (
                ContextRequirement(
                    key="distinct_sibling_logical_units",
                    max_items=max_chunks,
                ),
            )
        elif answer_shape == "procedure":
            requirements = (
                ContextRequirement(
                    key="primary_procedure",
                    max_items=max_chunks,
                ),
            )
        elif answer_shape == "responsibility":
            requirements = (ContextRequirement(key="primary_responsibility"),)
        else:
            requirements = (ContextRequirement(key="primary_fact"),)

        return ContextPlan(
            requested_fact_type=requested_fact_type,
            answer_shape=answer_shape,
            requirements=requirements,
            budget=ContextBudget(
                max_chunks=max_chunks,
                max_chars=max(1, int(max_context_chars)),
            ),
            score_ratio=max(0.0, min(float(score_ratio), 1.0)),
        )


class ContextComposer:
    """Compose bounded context while preserving retrieval candidate order."""

    def compose(
        self,
        question: str,
        candidates: Sequence[RetrievedChunk],
        *,
        plan: ContextPlan,
        base_selection: Sequence[RetrievedChunk] = (),
        candidate_provenance: Sequence[Mapping[str, Any]] = (),
    ) -> ContextSelection:
        del question  # Query semantics are already represented by the native plan.
        ordered_candidates = _dedupe_candidates(candidates)
        provenance = _provenance_by_chunk(candidate_provenance)
        selected: list[RetrievedChunk] = []
        selected_reasons: dict[str, tuple[str, ...]] = {}
        excluded_reasons: dict[str, str] = {}
        selected_coverage: set[str] = set()
        estimated_chars = 0

        def add(
            chunk: RetrievedChunk,
            reason: str,
            *,
            coverage_key: str | None = None,
        ) -> bool:
            nonlocal estimated_chars
            stable_key = _stable_chunk_key(chunk)
            if stable_key in selected_reasons:
                return False
            if coverage_key and coverage_key in selected_coverage:
                excluded_reasons.setdefault(stable_key, "duplicate_coverage")
                return False
            if len(selected) >= plan.budget.max_chunks:
                excluded_reasons.setdefault(stable_key, "lower_priority")
                return False
            item_chars = _estimated_context_chars(chunk)
            remaining_chars = plan.budget.max_chars - estimated_chars
            if selected and item_chars > remaining_chars:
                excluded_reasons.setdefault(stable_key, "budget_exceeded")
                return False
            if not selected and item_chars > remaining_chars:
                item_chars = remaining_chars
            selected.append(chunk)
            selected_reasons[stable_key] = (reason,)
            if coverage_key:
                selected_coverage.add(coverage_key)
            estimated_chars += item_chars
            return True

        coverage_required: dict[str, int] = {}
        coverage_satisfied: dict[str, bool] = {}

        if plan.answer_shape == "list":
            sibling_candidates = _sibling_cohort(
                ordered_candidates,
                provenance,
                preferred=base_selection,
            )
            required_count = min(len(sibling_candidates), plan.budget.max_chunks)
            coverage_required["distinct_sibling_logical_units"] = required_count
            for chunk in sibling_candidates:
                add(
                    chunk,
                    "required_sibling",
                    coverage_key=_coverage_key(chunk),
                )
            coverage_satisfied["distinct_sibling_logical_units"] = (
                required_count > 0
                and len(
                    {
                        _coverage_key(chunk)
                        for chunk in selected
                        if chunk in sibling_candidates
                    }
                )
                >= required_count
            )
        elif plan.answer_shape == "procedure":
            procedure_candidates = _procedure_candidates(
                ordered_candidates,
                provenance,
            )
            coverage_required["primary_procedure"] = (
                1 if procedure_candidates else 0
            )
            for index, chunk in enumerate(base_selection):
                add(
                    chunk,
                    "required_primary_fact" if index == 0 else "highest_score",
                )
            selected_coverage.update(
                _coverage_key(chunk) for chunk in selected
            )
            for index, chunk in enumerate(procedure_candidates):
                add(
                    chunk,
                    (
                        "required_primary_fact"
                        if not selected and index == 0
                        else "supporting_context"
                    ),
                    coverage_key=_coverage_key(chunk),
                )
            coverage_satisfied["primary_procedure"] = (
                not procedure_candidates
                or any(chunk in selected for chunk in procedure_candidates)
            )
        else:
            coverage_key = (
                "primary_responsibility"
                if plan.answer_shape == "responsibility"
                else "primary_fact"
            )
            coverage_required[coverage_key] = 1 if base_selection else 0
            for index, chunk in enumerate(base_selection):
                add(
                    chunk,
                    "required_primary_fact" if index == 0 else "supporting_context",
                )
            coverage_satisfied[coverage_key] = (
                not base_selection or bool(selected)
            )

        for chunk in base_selection:
            add(chunk, "highest_score")

        selected_keys = {_stable_chunk_key(chunk) for chunk in selected}
        selected_units = {_coverage_key(chunk) for chunk in selected}
        for chunk in ordered_candidates:
            stable_key = _stable_chunk_key(chunk)
            if stable_key in selected_keys or stable_key in excluded_reasons:
                continue
            if _coverage_key(chunk) in selected_units:
                excluded_reasons[stable_key] = "duplicate_coverage"
            elif len(selected) >= plan.budget.max_chunks:
                excluded_reasons[stable_key] = "lower_priority"
            elif (
                selected
                and estimated_chars + _estimated_context_chars(chunk)
                > plan.budget.max_chars
            ):
                excluded_reasons[stable_key] = "budget_exceeded"
            else:
                excluded_reasons[stable_key] = "not_required"

        return ContextSelection(
            plan=plan,
            selected=tuple(selected),
            selected_reasons=selected_reasons,
            excluded_reasons=excluded_reasons,
            coverage_required=coverage_required,
            coverage_satisfied=coverage_satisfied,
            estimated_chars=estimated_chars,
        )


def _answer_shape(requested_fact_type: str) -> str:
    if requested_fact_type == "list":
        return "list"
    if requested_fact_type == "procedure":
        return "procedure"
    if requested_fact_type in RESPONSIBILITY_FACT_TYPES:
        return "responsibility"
    if requested_fact_type == "definition":
        return "definition"
    if requested_fact_type == "comparison":
        return "comparison"
    if requested_fact_type in {"current_status", "current_value", "price", "availability"}:
        return "single_fact"
    return "default"


def _dedupe_candidates(
    chunks: Sequence[RetrievedChunk],
) -> list[RetrievedChunk]:
    seen: set[str] = set()
    result: list[RetrievedChunk] = []
    for chunk in chunks:
        key = _stable_chunk_key(chunk)
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


def _provenance_by_chunk(
    values: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        for key in _provenance_keys(value):
            result.setdefault(key, value)
    return result


def _provenance_keys(value: Mapping[str, Any]) -> tuple[str, ...]:
    keys: list[str] = []
    record_key = _clean(value.get("record_key"))
    if record_key:
        keys.append(f"record_key:{record_key}")
    chunk_id = value.get("chunk_id")
    if chunk_id is not None:
        keys.append(f"chunk_id:{chunk_id}")
    stable_key = _clean(value.get("stable_key"))
    if stable_key:
        keys.append(stable_key)
    source = _clean(value.get("source"))
    section = _clean(value.get("section"))
    if source or section:
        keys.append(f"source:{source}|section:{section}")
    return tuple(keys)


def _candidate_provenance(
    chunk: RetrievedChunk,
    provenance: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    for key in _chunk_lookup_keys(chunk):
        value = provenance.get(key)
        if value is not None:
            return value
    return {}


def _has_stage(
    chunk: RetrievedChunk,
    provenance: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> bool:
    value = _candidate_provenance(chunk, provenance)
    hits = value.get("stage_hits") if isinstance(value, Mapping) else None
    if isinstance(hits, Sequence) and not isinstance(hits, (str, bytes)):
        for hit in hits:
            if isinstance(hit, Mapping) and hit.get("stage") == stage:
                return True
    return any(
        reason == f"retrieval stage {stage}"
        for reason in (chunk.selection_reasons or [])
    )


def _sibling_cohort(
    candidates: Sequence[RetrievedChunk],
    provenance: Mapping[str, Mapping[str, Any]],
    *,
    preferred: Sequence[RetrievedChunk] = (),
) -> list[RetrievedChunk]:
    seed = next(
        (
            chunk
            for chunk in [*preferred, *candidates]
            if _has_stage(chunk, provenance, "sibling_lookup")
        ),
        None,
    )
    if seed is None:
        return []
    seed_source = _clean(seed.source)
    seed_type = _clean((seed.metadata or {}).get("logical_unit_type"))
    cohort = [
        chunk
        for chunk in candidates
        if _clean(chunk.source) == seed_source
        and _clean((chunk.metadata or {}).get("logical_unit_type")) == seed_type
        and _has_stage(chunk, provenance, "sibling_lookup")
    ]
    distinct = _distinct_coverage(cohort)
    return distinct if len(distinct) >= 2 else []


def _procedure_candidates(
    candidates: Sequence[RetrievedChunk],
    provenance: Mapping[str, Mapping[str, Any]],
) -> list[RetrievedChunk]:
    procedural = [
        chunk
        for chunk in candidates
        if _clean((chunk.metadata or {}).get("logical_unit_type")) == "procedure"
        or _has_stage(chunk, provenance, "procedure_lookup")
    ]
    return _distinct_coverage(procedural)


def _distinct_coverage(
    candidates: Sequence[RetrievedChunk],
) -> list[RetrievedChunk]:
    seen: set[str] = set()
    result: list[RetrievedChunk] = []
    for chunk in candidates:
        key = _coverage_key(chunk)
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


def _coverage_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    record_key = _clean(metadata.get("record_key"))
    if record_key:
        return f"record_key:{record_key}"
    source = _clean(chunk.source)
    logical_unit_title = _clean(metadata.get("logical_unit_title"))
    logical_unit_type = _clean(metadata.get("logical_unit_type"))
    if logical_unit_title:
        return (
            f"source:{source}|logical_unit_type:{logical_unit_type}"
            f"|logical_unit_title:{logical_unit_title}"
        )
    return f"source:{source}|section:{_clean(chunk.section)}"


def _stable_chunk_key(chunk: RetrievedChunk) -> str:
    metadata = chunk.metadata or {}
    record_key = _clean(metadata.get("record_key"))
    if record_key:
        return f"record_key:{record_key}"
    if chunk.chunk_id is not None:
        return (
            f"chunk_id:{chunk.chunk_id}|source:{_clean(chunk.source)}"
            f"|section:{_clean(chunk.section)}"
        )
    content_hash = _clean(metadata.get("content_hash"))
    if content_hash:
        return f"content_hash:{content_hash}"
    return f"source:{_clean(chunk.source)}|section:{_clean(chunk.section)}"


def _chunk_lookup_keys(chunk: RetrievedChunk) -> tuple[str, ...]:
    stable = _stable_chunk_key(chunk)
    keys = [stable]
    if chunk.chunk_id is not None:
        keys.append(f"chunk_id:{chunk.chunk_id}")
    source_section = f"source:{_clean(chunk.source)}|section:{_clean(chunk.section)}"
    if source_section not in keys:
        keys.append(source_section)
    return tuple(keys)


def _estimated_context_chars(chunk: RetrievedChunk) -> int:
    text = " ".join((chunk.text or "").split())
    if len(text) > _LONG_CHUNK_THRESHOLD and (
        chunk.matched_excerpt or chunk.matched_terms
    ):
        return len(chunk.matched_excerpt) or min(
            len(text),
            _LONG_CHUNK_EXCERPT_CHARS,
        )
    return len(text)


def _chunk_diagnostic(
    chunk: RetrievedChunk,
    *,
    reasons: Sequence[str],
) -> dict[str, Any]:
    metadata = chunk.metadata or {}
    return {
        "stable_key": _stable_chunk_key(chunk),
        "chunk_id": chunk.chunk_id,
        "record_key": metadata.get("record_key"),
        "title": chunk.title,
        "source": chunk.source,
        "section": chunk.section,
        "doc_type": chunk.doc_type or metadata.get("doc_type"),
        "logical_unit_type": metadata.get("logical_unit_type"),
        "logical_unit_title": metadata.get("logical_unit_title"),
        "estimated_chars": _estimated_context_chars(chunk),
        "reasons": list(reasons),
    }


def _clean(value: Any) -> str:
    return str(value or "").strip()
