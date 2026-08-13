# LineHelper Autonomous Runtime Probe — Level 1 Technical Report

## 1. Run configuration
- run_id: test-run
- started_at: now
- git_branch: test
- git_commit: abc
- seed_pack: pack.csv
- mode: mixed
- duration_minutes: 1
- max_questions: 10
- random_seed: 1

## 2. Runtime architecture actually tested
`question → Query Analyzer → QueryPlan → retrieval → evidence gate → final LLM → answer and sources`
The production `RagAnswerGenerator.answer()` path was called directly. The legacy feature flag was removed; no direct analyzer-only path and no memory writes were used.

## 3. Overall results
- status: RUNNING
- processed: 1
- PASS: 1
- WARN: 0
- FAIL: 0
- ERROR: 0
- average latency: 1.0 sec
- median latency: 1.0 sec

## 4. Results by diagnostic group
| diagnostic group | total | PASS | WARN | FAIL | ERROR |
|---|---:|---:|---:|---:|---:|
| G01 | 1 | 1 | 0 | 0 | 0 |

## 5. Results by generation strategy
| strategy | total | PASS | WARN | FAIL | ERROR |
|---|---:|---:|---:|---:|---:|
| seed | 1 | 1 | 0 | 0 | 0 |

## 6. Intent distribution
| value | count |
|---|---:|
| org_structure | 1 |

## 7. Response-kind distribution
| value | count |
|---|---:|
| answer | 1 |

## 8. Source usage and source anomalies
| value | count |
|---|---:|
| Оргсхема | 1 |

Source-related flagged cases: 0.

## 9. Query Analyzer quality signals
- query_plan present: 1/1
- fallback used: 0
- missing/empty expansions: 0

## 10. Critical failures
No cases.

## 11. Warning patterns
No data.

## 12. Slowest questions
| id | latency sec | analyzer | retrieval | answer | verdict | question |
|---|---:|---:|---:|---:|---|---|
| Q1 | 1.0 | 0.2 | 0.1 | 0.7 | PASS | Какие отделы есть в компании? |

## 13. Repeated failure clusters
No repeated critical clusters.

## 14. Most suspicious cases for level-2 review
- **Q1** [PASS] strategy=seed, intent=org_structure, source=Оргсхема, flags=-: Какие отделы есть в компании?

## 15. First-level technical conclusion
The run completed 1 question(s) with 0 critical FAIL/ERROR case(s). This is a first-level technical classification only; corporate-content correctness must be reviewed from answers_full.md together with sources and diagnostics.

Contextual follow-up note: the current `RagAnswerGenerator.answer(question)` call has no conversation-history argument. Follow-up sequences were recorded in run configuration but excluded from single-question quality metrics.
