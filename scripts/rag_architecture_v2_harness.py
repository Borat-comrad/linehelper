"""Deterministic evaluation helpers for the RAG architecture v2 test pack.

This module is test infrastructure. It evaluates architectural invariants and
serializes reports, but it does not implement or replace any production RAG
decision.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "rag_architecture_v2_cases.json"
STATUS_VALUES = ("passed", "failed", "blocked", "not_applicable")
OPERATIONAL_INTENT = "one_c_operational_lookup"
EXACT_TEXT_KEYS = frozenset({"exact_answer", "expected_answer", "answer_text"})


class FixtureValidationError(ValueError):
    """Raised when the architecture fixture does not satisfy its schema."""


def unavailable(reason: str) -> dict[str, Any]:
    """Return the stable marker used for absent production observability."""
    return {"available": False, "reason": reason}


def is_available(value: Any) -> bool:
    """Return whether a diagnostic value is actually observable."""
    return not (
        isinstance(value, Mapping)
        and value.get("available") is False
    )


def load_fixture(path: Path | str = DEFAULT_FIXTURE) -> dict[str, Any]:
    """Load and validate the machine-readable architecture pack."""
    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    validate_fixture(payload)
    return payload


def validate_fixture(payload: Any) -> None:
    """Validate the stable, dependency-free JSON fixture contract."""
    if not isinstance(payload, dict):
        raise FixtureValidationError("fixture root must be an object")
    if payload.get("schema_version") != "2.0":
        raise FixtureValidationError("schema_version must be '2.0'")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise FixtureValidationError("cases must be a non-empty list")

    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, dict):
            raise FixtureValidationError(f"{where} must be an object")
        case_id = _required_text(case, "id", where)
        if case_id in seen_ids:
            raise FixtureValidationError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        _required_text(case, "group", where)

        messages = case.get("messages")
        if not isinstance(messages, list) or not messages:
            raise FixtureValidationError(f"{where}.messages must be a non-empty list")
        if not any(
            isinstance(message, dict) and message.get("role") == "user"
            for message in messages
        ):
            raise FixtureValidationError(f"{where}.messages must contain a user message")
        for message_index, message in enumerate(messages):
            message_where = f"{where}.messages[{message_index}]"
            if not isinstance(message, dict):
                raise FixtureValidationError(f"{message_where} must be an object")
            if message.get("role") not in {"user", "assistant", "system"}:
                raise FixtureValidationError(f"{message_where}.role is invalid")
            _required_text(message, "content", message_where)

        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise FixtureValidationError(f"{where}.expected must be an object")
        forbidden_exact_keys = EXACT_TEXT_KEYS.intersection(expected)
        if forbidden_exact_keys:
            names = ", ".join(sorted(forbidden_exact_keys))
            raise FixtureValidationError(
                f"{where}.expected uses forbidden exact-text field(s): {names}"
            )
        for field in (
            "answerability",
            "requested_fact_type",
            "intent",
            "operational_lookup",
            "clarification",
            "final_modes",
            "required_retrieval",
            "required_context",
            "forbidden_behaviors",
        ):
            if field not in expected:
                raise FixtureValidationError(f"{where}.expected.{field} is required")
        if not isinstance(expected["operational_lookup"], bool):
            raise FixtureValidationError(
                f"{where}.expected.operational_lookup must be boolean"
            )
        if not isinstance(expected["clarification"], bool):
            raise FixtureValidationError(
                f"{where}.expected.clarification must be boolean"
            )
        if not _as_expected_values(expected["intent"]):
            raise FixtureValidationError(f"{where}.expected.intent must not be empty")
        if not _as_expected_values(expected["final_modes"]):
            raise FixtureValidationError(f"{where}.expected.final_modes must not be empty")
        for stage in ("required_retrieval", "required_context"):
            _validate_artifact_expectation(expected[stage], f"{where}.expected.{stage}")


def select_cases(
    fixture: Mapping[str, Any],
    *,
    case_ids: Sequence[str] | None = None,
    group: str | None = None,
) -> list[dict[str, Any]]:
    """Select cases while preserving fixture order."""
    cases = [dict(case) for case in fixture["cases"]]
    requested = {value.casefold() for value in case_ids or []}
    selected = [
        case
        for case in cases
        if (not requested or str(case["id"]).casefold() in requested)
        and (group is None or str(case["group"]).casefold() == group.casefold())
    ]
    if requested:
        found = {str(case["id"]).casefold() for case in selected}
        missing = sorted(requested - found)
        if missing:
            raise FixtureValidationError(
                "unknown case id(s): " + ", ".join(missing)
            )
    return selected


class RecordingRetriever:
    """Transparent wrapper that records production retriever calls and results."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.calls = []

    def retrieve(self, question: str, **kwargs: Any) -> Any:
        chunks = self.inner.retrieve(question, **kwargs)
        serialized = []
        for rank, chunk in enumerate(chunks, start=1):
            item = chunk_to_dict(chunk)
            item["retrieval_rank"] = rank
            item["retrieval_query"] = question
            serialized.append(item)
        self.calls.append(
            {
                "query": question,
                "parameters": _json_safe(kwargs),
                "chunks": serialized,
            }
        )
        return chunks

    def flattened_candidates(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for call_index, call in enumerate(self.calls, start=1):
            for item in call["chunks"]:
                candidate = dict(item)
                candidate["retrieval_call"] = call_index
                result.append(candidate)
        return result


def chunk_to_dict(chunk: Any) -> dict[str, Any]:
    """Serialize a RetrievedChunk-like value without depending on its class."""
    data = _object_mapping(chunk)
    metadata = data.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    final_score = data.get("final_score")
    score = final_score if final_score is not None else data.get("score")
    return {
        "chunk_id": data.get("chunk_id"),
        "title": str(data.get("title") or ""),
        "source": str(data.get("source") or ""),
        "section": data.get("section"),
        "page": data.get("page"),
        "doc_type": data.get("doc_type") or metadata.get("doc_type"),
        "score": score,
        "base_score": data.get("base_score"),
        "rerank_score": data.get("rerank_score"),
        "final_score": data.get("final_score"),
        "record_key": metadata.get("record_key"),
        "metadata": _json_safe(dict(metadata)),
        "matched_excerpt": str(data.get("matched_excerpt") or ""),
        "selection_reasons": _json_safe(data.get("selection_reasons") or []),
    }


def source_to_dict(source: Any) -> dict[str, Any]:
    """Serialize a RagSource-like value."""
    data = _object_mapping(source)
    return {
        "title": str(data.get("title") or ""),
        "source": str(data.get("source") or ""),
        "section": data.get("section"),
        "page": data.get("page"),
        "logical_unit_title": data.get("logical_unit_title"),
        "score": data.get("score"),
        "matched_excerpt": str(data.get("matched_excerpt") or ""),
    }


def selected_context_from_sources(
    sources: Sequence[Any],
    raw_candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Best-effort join of exposed RagSource values back to recorded chunks."""
    remaining = [dict(candidate) for candidate in raw_candidates]
    selected: list[dict[str, Any]] = []
    for source_value in sources:
        source = source_to_dict(source_value)
        best_index = _matching_candidate_index(source, remaining)
        if best_index is None:
            item = dict(source)
            item.update(
                {
                    "chunk_id": None,
                    "record_key": None,
                    "metadata": unavailable(
                        "RagSource does not expose chunk metadata and no recorded candidate matched"
                    ),
                    "selected_via": "rag_source_only",
                }
            )
        else:
            item = remaining.pop(best_index)
            item["selected_via"] = "rag_source_candidate_join"
        item["context_rank"] = len(selected) + 1
        selected.append(item)
    return selected


def evaluate_case(
    case: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate target invariants without comparing exact final answer text."""
    if case["expected"].get("not_applicable") is True:
        return {"status": "not_applicable", "failure_reasons": [], "checks": []}
    if diagnostic.get("status") == "blocked":
        reasons = list(diagnostic.get("failure_reasons") or [])
        if not reasons:
            reasons = ["live runtime is blocked"]
        return {"status": "blocked", "failure_reasons": reasons, "checks": []}

    expected = case["expected"]
    checks: list[dict[str, Any]] = []

    _check_value(
        checks,
        "requested_fact_type",
        expected["requested_fact_type"],
        diagnostic.get("requested_fact_type"),
    )
    _check_membership(
        checks,
        "intent",
        _as_expected_values(expected["intent"]),
        diagnostic.get("intent"),
    )
    _check_boolean_mapping(
        checks,
        "clarification",
        expected["clarification"],
        diagnostic.get("clarification"),
        "needed",
    )
    _check_boolean_mapping(
        checks,
        "operational_lookup",
        expected["operational_lookup"],
        diagnostic.get("operational_boundary"),
        "operational_lookup",
    )

    if "resolved_question" in expected:
        _check_value(
            checks,
            "resolved_question",
            expected["resolved_question"],
            diagnostic.get("resolved_question"),
            normalize_text=True,
        )

    _check_membership(
        checks,
        "final_mode",
        _as_expected_values(expected["final_modes"]),
        diagnostic.get("response_kind"),
    )
    _check_artifacts(
        checks,
        "retrieval",
        expected["required_retrieval"],
        diagnostic.get("raw_candidates"),
    )
    _check_artifacts(
        checks,
        "context",
        expected["required_context"],
        diagnostic.get("selected_context"),
    )
    _check_answer_concepts(
        checks,
        expected.get("required_answer_concepts") or [],
        diagnostic.get("answer"),
    )
    _check_forbidden_answer_terms(
        checks,
        expected.get("forbidden_answer_terms") or [],
        diagnostic.get("answer"),
    )
    _check_turns(checks, expected.get("turns") or [], diagnostic.get("turns"))
    _check_detectable_forbidden_behaviors(
        checks,
        expected.get("forbidden_behaviors") or [],
        diagnostic,
    )

    if not checks:
        status = "not_applicable"
    elif all(check["passed"] for check in checks):
        status = "passed"
    else:
        status = "failed"
    failure_reasons = [
        str(check["reason"])
        for check in checks
        if not check["passed"]
    ]
    return {
        "status": status,
        "failure_reasons": failure_reasons,
        "checks": checks,
    }


def apply_evaluation(
    case: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a copy of a diagnostic record with target evaluation fields."""
    record = dict(diagnostic)
    evaluation = evaluate_case(case, record)
    record["status"] = evaluation["status"]
    record["failure_reasons"] = evaluation["failure_reasons"]
    record["evaluation"] = {"checks": evaluation["checks"]}
    return record


def aggregate_metrics(
    cases: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate baseline metrics while preserving unknown as not_available."""
    case_by_id = {str(case["id"]): case for case in cases}
    selected_records = [
        record for record in records if str(record.get("case_id")) in case_by_id
    ]
    statuses = Counter(str(record.get("status") or "not_applicable") for record in selected_records)
    unique_ids = {str(record.get("case_id")) for record in selected_records}

    metrics: dict[str, Any] = {
        "total_cases": len(unique_ids),
        "total_evaluations": len(selected_records),
        "passed": statuses["passed"],
        "failed": statuses["failed"],
        "blocked": statuses["blocked"],
        "not_applicable": statuses["not_applicable"],
        "false_clarification_count": _boolean_mismatch_count(
            case_by_id,
            selected_records,
            expected_key="clarification",
            diagnostic_key="clarification",
            nested_key="needed",
            expected_value=False,
            actual_value=True,
        ),
        "missing_clarification_count": _boolean_mismatch_count(
            case_by_id,
            selected_records,
            expected_key="clarification",
            diagnostic_key="clarification",
            nested_key="needed",
            expected_value=True,
            actual_value=False,
        ),
        "operational_misroute_count": _boolean_mismatch_count(
            case_by_id,
            selected_records,
            expected_key="operational_lookup",
            diagnostic_key="operational_boundary",
            nested_key="operational_lookup",
            expected_value=False,
            actual_value=True,
        ),
        "required_chunk_recall_at_5": _required_artifact_rate(
            case_by_id,
            selected_records,
            stage="required_retrieval",
            actual_key="raw_candidates",
            rank_limit=5,
        ),
        "required_chunk_recall_at_10": _required_artifact_rate(
            case_by_id,
            selected_records,
            stage="required_retrieval",
            actual_key="raw_candidates",
            rank_limit=10,
        ),
        "required_chunk_in_context_rate": _required_artifact_rate(
            case_by_id,
            selected_records,
            stage="required_context",
            actual_key="selected_context",
            rank_limit=None,
        ),
        "evidence_coverage_rate": _evidence_coverage_rate(selected_records),
        "unsupported_person_answer_count": _unsupported_person_count(
            case_by_id, selected_records
        ),
        "generic_no_answer_when_evidence_exists": _generic_no_answer_count(
            case_by_id, selected_records
        ),
        "multi_turn_resolution_rate": _multi_turn_resolution_rate(
            case_by_id, selected_records
        ),
    }
    metrics["T01_T09_pass_rate"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "core" in case.get("tags", []),
    )
    metrics["paired_intent_pass_rate"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "paired" in case.get("tags", []),
    )
    metrics["conversation_cases"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "conversation" in case.get("tags", [])
        or "conversation_seed" in case.get("tags", []),
    )
    metrics["responsibility_cases"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "responsibility" in case.get("tags", []),
    )
    metrics["procedure_cases"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "procedure" in case.get("tags", []),
    )
    metrics["operational_boundary_cases"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "operational_boundary" in case.get("tags", []),
    )
    metrics["clarification_cases"] = _group_pass_rate(
        case_by_id,
        selected_records,
        lambda case: "clarification" in case.get("tags", [])
        or "clarification_negative" in case.get("tags", []),
    )
    return metrics


def build_repeatability(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare architectural decisions across repeated live runs."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("case_id") or "")].append(record)

    result: dict[str, Any] = {}
    for case_id, case_records in grouped.items():
        if len(case_records) < 2:
            continue
        dimensions = {
            "intent": [_observable_scalar(record.get("intent")) for record in case_records],
            "clarification": [
                _observable_nested(record.get("clarification"), "needed")
                for record in case_records
            ],
            "operational_routing": [
                _observable_nested(
                    record.get("operational_boundary"), "operational_lookup"
                )
                for record in case_records
            ],
            "context_chunks": [
                _context_signature(record.get("selected_context"))
                for record in case_records
            ],
            "response_kind": [
                _observable_scalar(record.get("response_kind"))
                for record in case_records
            ],
        }
        max_turns = max(
            (
                len(record.get("turns"))
                if isinstance(record.get("turns"), list)
                else 0
            )
            for record in case_records
        )
        for turn_index in range(max_turns):
            turn_number = turn_index + 1
            dimensions[f"turn_{turn_number}_intent"] = [
                _observable_scalar(_turn(record, turn_index).get("intent"))
                for record in case_records
            ]
            dimensions[f"turn_{turn_number}_clarification"] = [
                _observable_nested(
                    _turn(record, turn_index).get("clarification"),
                    "needed",
                )
                for record in case_records
            ]
            dimensions[f"turn_{turn_number}_operational_routing"] = [
                _observable_nested(
                    _turn(record, turn_index).get("operational_boundary"),
                    "operational_lookup",
                )
                for record in case_records
            ]
            dimensions[f"turn_{turn_number}_context_chunks"] = [
                _context_signature(
                    _turn(record, turn_index).get("selected_context")
                )
                for record in case_records
            ]
            dimensions[f"turn_{turn_number}_response_kind"] = [
                _observable_scalar(
                    _turn(record, turn_index).get("response_kind")
                )
                for record in case_records
            ]
        comparisons = {
            name: _repeatability_dimension(values)
            for name, values in dimensions.items()
        }
        available = [
            value["stable"]
            for value in comparisons.values()
            if value["stable"] != "not_available"
        ]
        result[case_id] = {
            "repeats": len(case_records),
            "dimensions": comparisons,
            "all_available_dimensions_stable": (
                all(available) if available else "not_available"
            ),
        }
    return result or {"available": False, "reason": "no repeated cases in this run"}


def write_json(path: Path, value: Any) -> None:
    """Write stable UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Write UTF-8 JSON Lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(_json_safe(record), ensure_ascii=False) + "\n")


def render_summary_markdown(
    *,
    title: str,
    metrics: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    repeatability: Mapping[str, Any] | None = None,
) -> str:
    """Render a compact human-readable summary without exact-text assertions."""
    lines = [
        f"# {title}",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key, value in metrics.items():
        lines.append(f"| {_md(key)} | {_md(_compact(value))} |")

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Status | Intent | Mode | Reasons |",
            "|---|---|---|---|---|",
        ]
    )
    for record in records:
        intent = _observable_scalar(record.get("intent"))
        mode = _observable_scalar(record.get("response_kind"))
        reasons = "; ".join(str(value) for value in record.get("failure_reasons") or [])
        lines.append(
            "| {case} | {status} | {intent} | {mode} | {reasons} |".format(
                case=_md(record.get("case_id")),
                status=_md(record.get("status")),
                intent=_md(intent),
                mode=_md(mode),
                reasons=_md(reasons),
            )
        )

    if repeatability is not None:
        lines.extend(
            [
                "",
                "## Repeatability",
                "",
                "```json",
                json.dumps(_json_safe(repeatability), ensure_ascii=False, indent=2),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _required_text(value: Mapping[str, Any], key: str, where: str) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field.strip():
        raise FixtureValidationError(f"{where}.{key} must be a non-empty string")
    return field


def _validate_artifact_expectation(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise FixtureValidationError(f"{where} must be an object")
    for field in ("sources", "record_keys", "chunk_ids"):
        if field not in value or not isinstance(value[field], list):
            raise FixtureValidationError(f"{where}.{field} must be a list")


def _object_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    names = (
        "chunk_id",
        "title",
        "source",
        "section",
        "page",
        "text",
        "score",
        "metadata",
        "doc_type",
        "base_score",
        "rerank_score",
        "final_score",
        "matched_terms",
        "matched_excerpt",
        "selection_reasons",
        "logical_unit_title",
    )
    return {name: getattr(value, name, None) for name in names}


def _matching_candidate_index(
    source: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> int | None:
    for index, candidate in enumerate(candidates):
        if (
            _normalize(source.get("title")) == _normalize(candidate.get("title"))
            and _normalize(source.get("source")) == _normalize(candidate.get("source"))
            and _normalize(source.get("section")) == _normalize(candidate.get("section"))
        ):
            source_score = source.get("score")
            candidate_score = candidate.get("score")
            if source_score is None or candidate_score is None:
                return index
            try:
                if abs(float(source_score) - float(candidate_score)) < 0.001:
                    return index
            except (TypeError, ValueError):
                return index
    for index, candidate in enumerate(candidates):
        if (
            _normalize(source.get("title")) == _normalize(candidate.get("title"))
            and _normalize(source.get("source")) == _normalize(candidate.get("source"))
        ):
            return index
    return None


def _check_value(
    checks: list[dict[str, Any]],
    name: str,
    expected: Any,
    actual: Any,
    *,
    normalize_text: bool = False,
) -> None:
    if actual is None or not is_available(actual):
        checks.append(_failed_check(name, expected, actual, f"{name} is not observable"))
        return
    comparable_expected = _normalize(expected) if normalize_text else expected
    comparable_actual = _normalize(actual) if normalize_text else actual
    passed = comparable_actual == comparable_expected
    reason = (
        f"{name} matched"
        if passed
        else f"{name}: expected {expected!r}, got {actual!r}"
    )
    checks.append(_check(name, passed, expected, actual, reason))


def _check_membership(
    checks: list[dict[str, Any]],
    name: str,
    expected: Sequence[Any],
    actual: Any,
) -> None:
    if actual is None or not is_available(actual):
        checks.append(_failed_check(name, list(expected), actual, f"{name} is not observable"))
        return
    passed = actual in expected
    reason = (
        f"{name} matched"
        if passed
        else f"{name}: expected one of {list(expected)!r}, got {actual!r}"
    )
    checks.append(_check(name, passed, list(expected), actual, reason))


def _check_boolean_mapping(
    checks: list[dict[str, Any]],
    name: str,
    expected: bool,
    actual_mapping: Any,
    key: str,
) -> None:
    if (
        not isinstance(actual_mapping, Mapping)
        or not is_available(actual_mapping)
        or not isinstance(actual_mapping.get(key), bool)
    ):
        checks.append(
            _failed_check(name, expected, actual_mapping, f"{name} is not observable")
        )
        return
    actual = bool(actual_mapping[key])
    passed = actual is expected
    reason = (
        f"{name} matched"
        if passed
        else f"{name}: expected {expected!r}, got {actual!r}"
    )
    checks.append(_check(name, passed, expected, actual, reason))


def _check_artifacts(
    checks: list[dict[str, Any]],
    stage: str,
    expected: Mapping[str, Any],
    actual: Any,
) -> None:
    required = _artifact_expectations(expected)
    if not required:
        return
    if not isinstance(actual, list):
        for kind, value in required:
            checks.append(
                _failed_check(
                    f"{stage}_{kind}",
                    value,
                    actual,
                    f"{stage} diagnostics are not observable for required {kind} {value!r}",
                )
            )
        return
    for kind, value in required:
        passed = _artifact_present(kind, value, actual)
        reason = (
            f"required {kind} {value!r} is present in {stage}"
            if passed
            else f"required {kind} {value!r} is missing from {stage}"
        )
        checks.append(
            _check(f"{stage}_{kind}", passed, value, _artifact_preview(actual), reason)
        )


def _check_answer_concepts(
    checks: list[dict[str, Any]],
    concepts: Sequence[Any],
    answer: Any,
) -> None:
    if not concepts:
        return
    if not isinstance(answer, str):
        for concept in concepts:
            checks.append(
                _failed_check(
                    "answer_concept",
                    concept,
                    answer,
                    "answer is not observable for concept evaluation",
                )
            )
        return
    normalized_answer = _normalize(answer)
    for alternatives in concepts:
        values = _as_expected_values(alternatives)
        passed = any(_normalize(value) in normalized_answer for value in values)
        reason = (
            f"answer covers concept {values!r}"
            if passed
            else f"answer misses concept alternatives {values!r}"
        )
        checks.append(_check("answer_concept", passed, values, "<answer omitted>", reason))


def _check_forbidden_answer_terms(
    checks: list[dict[str, Any]],
    terms: Sequence[Any],
    answer: Any,
) -> None:
    if not terms:
        return
    if not isinstance(answer, str):
        checks.append(
            _failed_check(
                "forbidden_answer_terms",
                list(terms),
                answer,
                "answer is not observable for unsupported-person evaluation",
            )
        )
        return
    normalized_answer = _normalize(answer)
    found = [str(term) for term in terms if _normalize(term) in normalized_answer]
    passed = not found
    reason = (
        "no forbidden answer term is present"
        if passed
        else "unsupported/forbidden answer term(s): " + ", ".join(found)
    )
    checks.append(
        _check(
            "forbidden_answer_terms",
            passed,
            list(terms),
            found,
            reason,
        )
    )


def _check_turns(
    checks: list[dict[str, Any]],
    expected_turns: Sequence[Mapping[str, Any]],
    actual_turns: Any,
) -> None:
    if not expected_turns:
        return
    if not isinstance(actual_turns, list):
        checks.append(
            _failed_check(
                "turns",
                list(expected_turns),
                actual_turns,
                "turn diagnostics are not observable",
            )
        )
        return
    for expectation in expected_turns:
        turn_number = expectation.get("turn")
        if not isinstance(turn_number, int) or turn_number < 1 or turn_number > len(actual_turns):
            checks.append(
                _failed_check(
                    "turn",
                    turn_number,
                    len(actual_turns),
                    f"turn {turn_number!r} is missing",
                )
            )
            continue
        actual = actual_turns[turn_number - 1]
        if "clarification" in expectation:
            _check_boolean_mapping(
                checks,
                f"turn_{turn_number}_clarification",
                bool(expectation["clarification"]),
                actual.get("clarification") if isinstance(actual, Mapping) else None,
                "needed",
            )
        if "ambiguity_span" in expectation:
            span = (
                actual.get("clarification", {}).get("ambiguity_span")
                if isinstance(actual, Mapping)
                and isinstance(actual.get("clarification"), Mapping)
                else None
            )
            _check_value(
                checks,
                f"turn_{turn_number}_ambiguity_span",
                expectation["ambiguity_span"],
                span,
                normalize_text=True,
            )


def _check_detectable_forbidden_behaviors(
    checks: list[dict[str, Any]],
    behaviors: Sequence[str],
    diagnostic: Mapping[str, Any],
) -> None:
    operational = _observable_nested(
        diagnostic.get("operational_boundary"), "operational_lookup"
    )
    clarification = _observable_nested(diagnostic.get("clarification"), "needed")
    response_kind = _observable_scalar(diagnostic.get("response_kind"))
    selected_context = diagnostic.get("selected_context")
    detectable: dict[str, tuple[bool | None, str]] = {
        "route_to_one_c": (
            operational is True,
            "responsibility/static question was routed to operational lookup",
        ),
        "route_to_current_status": (
            operational is True,
            "static question was routed to current status",
        ),
        "empty_context": (
            isinstance(selected_context, list) and not selected_context,
            "selected context is empty",
        ),
        "generic_no_answer": (
            response_kind == "no_answer",
            "runtime returned generic no_answer",
        ),
        "repeat_kp_clarification": (
            clarification is True,
            "final turn repeated clarification",
        ),
        "kp_ckp_clarification": (
            clarification is True,
            "runtime returned an unrelated clarification",
        ),
        "random_organization_answer": (
            clarification is False
            and isinstance(selected_context, list)
            and bool(selected_context),
            "ambiguous question proceeded with context instead of clarification",
        ),
    }
    for behavior in behaviors:
        if behavior not in detectable:
            continue
        occurred, reason = detectable[behavior]
        if occurred is None:
            checks.append(
                _failed_check(
                    f"forbidden_{behavior}",
                    False,
                    unavailable("required diagnostic is absent"),
                    f"cannot observe forbidden behavior {behavior}",
                )
            )
        else:
            checks.append(
                _check(
                    f"forbidden_{behavior}",
                    not occurred,
                    False,
                    occurred,
                    f"forbidden behavior {behavior} did not occur"
                    if not occurred
                    else reason,
                )
            )


def _check(
    name: str,
    passed: bool,
    expected: Any,
    actual: Any,
    reason: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": _json_safe(expected),
        "actual": _json_safe(actual),
        "reason": reason,
    }


def _failed_check(name: str, expected: Any, actual: Any, reason: str) -> dict[str, Any]:
    return _check(name, False, expected, actual, reason)


def _as_expected_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def _artifact_expectations(value: Mapping[str, Any]) -> list[tuple[str, Any]]:
    result: list[tuple[str, Any]] = []
    result.extend(("source", item) for item in value.get("sources") or [])
    result.extend(("record_key", item) for item in value.get("record_keys") or [])
    result.extend(("chunk_id", item) for item in value.get("chunk_ids") or [])
    return result


def _artifact_present(
    kind: str,
    value: Any,
    actual: Sequence[Mapping[str, Any]],
) -> bool:
    if kind == "chunk_id":
        return any(_same_int(item.get("chunk_id"), value) for item in actual)
    if kind == "record_key":
        return any(
            _normalize(item.get("record_key")) == _normalize(value)
            or (
                isinstance(item.get("metadata"), Mapping)
                and _normalize(item["metadata"].get("record_key")) == _normalize(value)
            )
            for item in actual
        )
    needle = _normalize(value)
    return any(
        needle in _normalize(
            " ".join(
                str(field or "")
                for field in (item.get("title"), item.get("source"))
            )
        )
        for item in actual
    )


def _artifact_preview(actual: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": item.get("chunk_id"),
            "record_key": item.get("record_key")
            or (
                item.get("metadata", {}).get("record_key")
                if isinstance(item.get("metadata"), Mapping)
                else None
            ),
            "title": item.get("title"),
            "source": item.get("source"),
        }
        for item in actual[:20]
    ]


def _boolean_mismatch_count(
    case_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    *,
    expected_key: str,
    diagnostic_key: str,
    nested_key: str,
    expected_value: bool,
    actual_value: bool,
) -> int | str:
    comparable = 0
    count = 0
    for record in records:
        if record.get("status") == "blocked":
            continue
        case = case_by_id[str(record["case_id"])]
        expected = case["expected"].get(expected_key)
        actual = _observable_nested(record.get(diagnostic_key), nested_key)
        if not isinstance(expected, bool) or not isinstance(actual, bool):
            continue
        comparable += 1
        if expected is expected_value and actual is actual_value:
            count += 1
    return count if comparable else "not_available"


def _required_artifact_rate(
    case_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    *,
    stage: str,
    actual_key: str,
    rank_limit: int | None,
) -> dict[str, Any] | str:
    found = 0
    required_count = 0
    for record in records:
        if record.get("status") == "blocked":
            continue
        case = case_by_id[str(record["case_id"])]
        required = _metric_artifact_expectations(case["expected"][stage])
        actual = record.get(actual_key)
        if not required or not isinstance(actual, list):
            continue
        if rank_limit is not None:
            actual = [
                item
                for item in actual
                if not isinstance(item.get("retrieval_rank"), int)
                or item["retrieval_rank"] <= rank_limit
            ]
        required_count += len(required)
        found += sum(
            1 for kind, value in required if _artifact_present(kind, value, actual)
        )
    if not required_count:
        return "not_available"
    return {
        "found": found,
        "required": required_count,
        "rate": round(found / required_count, 4),
    }


def _metric_artifact_expectations(
    value: Mapping[str, Any],
) -> list[tuple[str, Any]]:
    """Count stable chunk identities once; use source only as a fallback identity."""
    identities: list[tuple[str, Any]] = []
    identities.extend(("record_key", item) for item in value.get("record_keys") or [])
    identities.extend(("chunk_id", item) for item in value.get("chunk_ids") or [])
    if identities:
        return identities
    return [("source", item) for item in value.get("sources") or []]


def _evidence_coverage_rate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any] | str:
    values: list[float] = []
    for record in records:
        decision = record.get("evidence_decision")
        if not isinstance(decision, Mapping) or not is_available(decision):
            continue
        coverage = decision.get("coverage_rate")
        if isinstance(coverage, (int, float)):
            values.append(float(coverage))
    if not values:
        return "not_available"
    return {"evaluated": len(values), "rate": round(sum(values) / len(values), 4)}


def _unsupported_person_count(
    case_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> int | str:
    comparable = 0
    count = 0
    for record in records:
        case = case_by_id[str(record["case_id"])]
        terms = case["expected"].get("forbidden_answer_terms") or []
        answer = record.get("answer")
        if not terms or not isinstance(answer, str):
            continue
        comparable += 1
        normalized_answer = _normalize(answer)
        if any(_normalize(term) in normalized_answer for term in terms):
            count += 1
    return count if comparable else "not_available"


def _generic_no_answer_count(
    case_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> int | str:
    comparable = 0
    count = 0
    for record in records:
        case = case_by_id[str(record["case_id"])]
        required = _metric_artifact_expectations(
            case["expected"]["required_retrieval"]
        )
        actual = record.get("raw_candidates")
        if not required or not isinstance(actual, list):
            continue
        evidence_exists = any(
            _artifact_present(kind, value, actual) for kind, value in required
        )
        comparable += 1
        if evidence_exists and record.get("response_kind") == "no_answer":
            count += 1
    return count if comparable else "not_available"


def _multi_turn_resolution_rate(
    case_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | str:
    resolved = 0
    expected_count = 0
    observable = 0
    for record in records:
        case = case_by_id[str(record["case_id"])]
        expected = case["expected"].get("resolved_question")
        if expected is None:
            continue
        expected_count += 1
        actual = record.get("resolved_question")
        if actual is None or not is_available(actual):
            continue
        observable += 1
        if _normalize(actual) == _normalize(expected):
            resolved += 1
    if not expected_count or not observable:
        return "not_available"
    return {
        "resolved": resolved,
        "observable": observable,
        "expected": expected_count,
        "rate": round(resolved / observable, 4),
    }


def _group_pass_rate(
    case_by_id: Mapping[str, Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    predicate: Any,
) -> dict[str, Any] | str:
    selected = [
        record
        for record in records
        if predicate(case_by_id[str(record["case_id"])])
    ]
    if not selected:
        return "not_available"
    statuses = Counter(str(record.get("status")) for record in selected)
    evaluated = statuses["passed"] + statuses["failed"]
    return {
        "cases": len(selected),
        "passed": statuses["passed"],
        "failed": statuses["failed"],
        "blocked": statuses["blocked"],
        "not_applicable": statuses["not_applicable"],
        "rate": round(statuses["passed"] / evaluated, 4) if evaluated else "not_available",
    }


def _observable_scalar(value: Any) -> Any:
    if value is None or not is_available(value):
        return "not_available"
    return value


def _observable_nested(value: Any, key: str) -> Any:
    if not isinstance(value, Mapping) or not is_available(value):
        return "not_available"
    result = value.get(key)
    return result if result is not None else "not_available"


def _context_signature(value: Any) -> Any:
    if not isinstance(value, list):
        return "not_available"
    return [
        (
            item.get("chunk_id"),
            item.get("record_key"),
            item.get("title"),
            item.get("section"),
        )
        for item in value
    ]


def _turn(record: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    turns = record.get("turns")
    if not isinstance(turns, list) or index >= len(turns):
        return {}
    value = turns[index]
    return value if isinstance(value, Mapping) else {}


def _repeatability_dimension(values: Sequence[Any]) -> dict[str, Any]:
    if any(value == "not_available" for value in values):
        return {"stable": "not_available", "values": _json_safe(list(values))}
    normalized = [json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True) for value in values]
    return {
        "stable": len(set(normalized)) == 1,
        "values": _json_safe(list(values)),
    }


def _same_int(left: Any, right: Any) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _compact(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)
    return str(value)


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
