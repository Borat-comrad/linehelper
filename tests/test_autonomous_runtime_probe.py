from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_autonomous_runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("autonomous_runtime_probe", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_cli_defaults() -> None:
    args = probe._parse_args([])
    assert args.duration_minutes == 240
    assert args.max_questions == 500
    assert args.seed_pack == Path("docs/test_packs/linehelper_runtime_query_analyzer_test_pack_360.csv")
    assert args.out_dir == Path("data/test_runs/autonomous_runtime_probe")
    assert args.checkpoint_every == 25
    assert args.mode == "mixed"
    assert args.generator_batch_size == 10


def test_mixed_schedule_has_requested_twenty_item_distribution() -> None:
    strategies = [probe._strategy_for("mixed", index, 42) for index in range(1, 21)]
    assert Counter(strategies) == {
        "seed": 6,
        "mutation": 5,
        "noise": 3,
        "adversarial": 3,
        "generated": 3,
    }


def test_deterministic_mutation_and_noise_preserve_original_input() -> None:
    original = "Какие отделы есть в компании?"
    mutation, mutation_type = probe._mutate_question(original, 0)
    noise, noise_type = probe._noise_question(original, 3)
    assert original == "Какие отделы есть в компании?"
    assert mutation != original
    assert mutation_type
    assert noise != original
    assert noise_type == "user_noise"


def test_generated_json_parser_accepts_fenced_array() -> None:
    assert probe._parse_generated_questions('```json\n["Вопрос 1?", "Вопрос 2?"]\n```') == [
        "Вопрос 1?",
        "Вопрос 2?",
    ]


def test_missing_query_plan_is_critical_fail() -> None:
    record = _record()
    record["query_plan_present"] = False
    record["query_plan_enabled"] = None
    record["query_plan_intent"] = None
    verdict, flags = probe._evaluate_probe_record(record)
    assert verdict == "FAIL"
    assert "QUERY_PLAN_MISSING" in flags


def test_one_c_sources_are_critical_fail() -> None:
    record = _record()
    record.update({
        "question": "Есть ли остатки на складе?",
        "query_plan_intent": "one_c_operational_lookup",
        "sources_count": 1,
        "sources": [{"title": "Инструкция", "source": "doc"}],
        "sources_titles": ["Инструкция"],
        "top_source_title": "Инструкция",
    })
    verdict, flags = probe._evaluate_probe_record(record, known_sources={("Инструкция", "doc")})
    assert verdict == "FAIL"
    assert "ONE_C_WITH_SEMANTIC_SOURCES" in flags


def test_report_writer_creates_required_checkpoint_files(tmp_path: Path) -> None:
    config = {
        "run_id": "test-run", "max_questions": 10, "sleep": 0, "started_at": "now",
        "git_branch": "test", "git_commit": "abc", "seed_pack": "pack.csv", "mode": "mixed",
        "duration_minutes": 1, "random_seed": 1, "env": {},
    }
    probe._write_all_reports(
        tmp_path,
        [_record()],
        config,
        started_monotonic=probe.time.monotonic(),
        status="RUNNING",
    )
    for name in (
        probe.RESULTS_CSV, probe.ANSWERS_MD, probe.SUMMARY_JSON, probe.CHECKPOINT_MD,
        probe.REPORT_MD, probe.FAILURES_MD, probe.WARNINGS_MD, probe.SUSPICIOUS_MD,
    ):
        assert (tmp_path / name).exists(), name
    assert "answer_text" not in (tmp_path / probe.RESULTS_CSV).read_text(encoding="utf-8-sig").splitlines()[0]
    assert "Полный тестовый ответ" in (tmp_path / probe.ANSWERS_MD).read_text(encoding="utf-8")


def _record() -> dict[str, object]:
    return {
        "run_id": "test-run", "sequence_id": 1, "question_id": "Q1", "seed_question_id": "Q1",
        "diagnostic_group": "G01", "diagnostic_focus": "test", "generation_strategy": "seed",
        "mutation_type": "none", "original_question": "Какие отделы есть в компании?",
        "question": "Какие отделы есть в компании?", "expected_intent": "org_structure",
        "expected_response_kind": "answer", "expected_source_hint": "", "risk_tag": "known",
        "response_kind": "answer", "answer_text": "Полный тестовый ответ о структуре компании.",
        "answer_preview": "Полный тестовый ответ", "sources_count": 1,
        "sources_titles": ["Оргсхема"], "sources_sections": ["Структура"],
        "sources": [{"title": "Оргсхема", "source": "org.pdf", "section": "Структура"}],
        "top_source_title": "Оргсхема", "top_source_section": "Структура",
        "query_plan_present": True, "query_plan_enabled": True, "query_plan_intent": "org_structure",
        "query_plan_answer_type": "full_answer", "query_plan_normalized_question": "отделы компании",
        "query_plan_expansions": ["подразделения"], "query_plan_preferred_sources": ["Оргсхема"],
        "query_plan_confidence": 0.9, "query_plan_fallback_used": False, "query_plan_error": None,
        "diagnostic_candidates_count": 0, "diagnostic_candidate_titles": [], "diagnostic_candidates": [],
        "chunks_used": 1, "prompt_length": 100, "latency_total_sec": 1.0,
        "latency_analyzer_sec": 0.2, "latency_retrieval_sec": 0.1, "latency_answer_sec": 0.7,
        "verdict": "PASS", "flags": [], "error": "", "error_stage": "", "error_type": "",
        "error_message": "", "error_traceback": "", "traceback": "", "timestamp": "now",
    }
