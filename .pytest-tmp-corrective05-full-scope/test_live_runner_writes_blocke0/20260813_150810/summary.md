# RAG architecture v2 baseline run

## Metrics

| Metric | Value |
|---|---|
| total_cases | 1 |
| total_evaluations | 1 |
| passed | 0 |
| failed | 0 |
| blocked | 1 |
| not_applicable | 0 |
| false_clarification_count | not_available |
| missing_clarification_count | not_available |
| valid_clarification_recall | not_available |
| invalid_clarification_rejection_rate | not_available |
| missing_slot_clarification_accuracy | not_available |
| retrieval_started_after_rejected_clarification | not_available |
| operational_misroute_count | not_available |
| missed_operational_count | not_available |
| requested_fact_type_availability | not_available |
| requested_fact_type_accuracy | not_available |
| required_chunk_recall_at_5 | not_available |
| required_chunk_recall_at_10 | not_available |
| required_candidate_recall_at_5 | not_available |
| required_candidate_recall_at_10 | not_available |
| required_candidate_recall_by_stage | not_available |
| exact_identifier_recall | not_available |
| subject_function_recall | not_available |
| procedure_recall | not_available |
| organization_function_recall | not_available |
| sibling_recall | not_available |
| candidate_provenance_coverage | not_available |
| duplicate_candidates_before_merge | not_available |
| duplicate_candidates_after_merge | not_available |
| best_score_dedupe_accuracy | not_available |
| candidate_order_stability | {"available": false, "reason": "requires repeated execution; see repeatability artifact"} |
| retrieval_latency_p50 | not_available |
| retrieval_latency_p95 | not_available |
| required_chunk_in_context_rate | not_available |
| evidence_coverage_rate | not_available |
| unsupported_person_answer_count | not_available |
| generic_no_answer_when_evidence_exists | not_available |
| multi_turn_resolution_rate | not_available |
| resolved_question_available_rate | {"available": 0, "rate": 0.0, "total": 1} |
| resolved_question_accuracy | not_available |
| topic_change_accuracy | not_available |
| slot_inheritance_accuracy | not_available |
| stale_context_leak_count | not_available |
| history_used_when_required | not_available |
| history_used_when_not_required | not_available |
| repeated_clarification_after_resolution | not_available |
| T01_T09_pass_rate | {"blocked": 1, "cases": 1, "failed": 0, "not_applicable": 0, "passed": 0, "rate": "not_available"} |
| paired_intent_pass_rate | not_available |
| paired_responsibility_status_pass_rate | not_available |
| conversation_cases | not_available |
| conversation_cases_total | not_available |
| conversation_cases_passed | not_available |
| responsibility_cases | {"blocked": 1, "cases": 1, "failed": 0, "not_applicable": 0, "passed": 0, "rate": "not_available"} |
| procedure_cases | not_available |
| operational_boundary_cases | not_available |
| clarification_cases | not_available |

## Cases

| Case | Status | Intent | Mode | Reasons |
|---|---|---|---|---|
| T06 | blocked | not_available | not_available | Ollama unavailable |

## Repeatability

```json
{
  "available": false,
  "reason": "no repeated cases in this run"
}
```
