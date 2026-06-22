"""Manual smoke test for the full local read-only RAG chat flow."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator  # noqa: E402


QUESTIONS = [
    "Что говорится про планирование на неделю?",
    "Зачем нужны планы на неделю?",
    "Какие обязанности у сотрудника при планировании на неделю?",
    "Какие подразделения есть в коммерческом отделении?",
    "Какие отделения есть в компании?",
    "Что такое ЦКП компании Serviceline?",
    "Из чего состоит ЗРС?",
    "Как оформить распоряжение?",
]


def main() -> int:
    _configure_stdout()
    db_path = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"
    if not db_path.exists():
        print("Status: FAIL")
        print(f"Reason: active memory DB not found: {db_path}")
        return 1

    generator = RagAnswerGenerator(db_path=db_path)
    print("=== LOCAL RAG CHAT SMOKE TEST ===")
    print(f"DB: {db_path}")
    print(f"Model: {generator.llm_client.model}")
    print()

    errors = 0
    for index, question in enumerate(QUESTIONS, start=1):
        print(f"=== QUESTION {index}/{len(QUESTIONS)} ===")
        print(f"Question: {question}")
        try:
            result = generator.answer(question)
        except RagAnswerError as exc:
            errors += 1
            print("Status: ERROR")
            print(f"Reason: {exc}")
            print()
            continue
        except Exception as exc:
            errors += 1
            print("Status: ERROR")
            print(f"Reason: {type(exc).__name__}: {exc}")
            print()
            continue

        print("Answer:")
        print(result.answer)
        print()
        print("Sources:")
        if result.sources:
            for source_index, source in enumerate(result.sources, start=1):
                print(
                    f"[{source_index}] {source.title} | "
                    f"{source.section or '-'} | "
                    f"{source.logical_unit_title or '-'} | "
                    f"page {source.page if source.page is not None else '-'} | "
                    f"{source.source}"
                )
        else:
            print("-")
        print(f"Model: {result.model}")
        print(f"Prompt length: {result.prompt_length}")
        print(f"Elapsed seconds: {result.elapsed_seconds}")
        print("Status: OK")
        print()

    print("=== SUMMARY ===")
    print(f"Questions: {len(QUESTIONS)}")
    print(f"Errors: {errors}")
    print(f"Status: {'PASS' if errors == 0 else 'REVIEW'}")
    return 0 if errors == 0 else 1


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
