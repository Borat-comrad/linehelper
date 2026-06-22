"""Streamlit chat UI for the local read-only RAG MVP."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator  # noqa: E402


DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"


def main() -> None:
    st.set_page_config(page_title="LineHelper", page_icon="LH", layout="wide")
    st.title("LineHelper")
    st.caption("Локальный read-only RAG-chat по semantic memory Serviceline")

    if not DB_PATH.exists():
        st.error(
            "База semantic memory не найдена. Сначала пересоберите "
            "data/memory/linehelper_memory.db из curated chunks."
        )
        return

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "generator" not in st.session_state:
        st.session_state.generator = RagAnswerGenerator(db_path=DB_PATH)

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant" and message.get("result"):
                _render_result_details(message["result"])

    question = st.chat_input("Задайте вопрос по корпоративной базе знаний")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Ищу источники и спрашиваю локальную модель..."):
                result = st.session_state.generator.answer(question)
        except ValueError as exc:
            st.error(str(exc))
            return
        except RagAnswerError as exc:
            st.error(f"Не удалось получить ответ от локальной модели: {exc}")
            return
        except Exception as exc:
            st.error(f"Не удалось выполнить запрос: {exc}")
            return

        st.markdown(result.answer)
        _render_result_details(result)

    st.session_state.messages.append(
        {"role": "assistant", "content": result.answer, "result": result}
    )


def _render_result_details(result) -> None:
    if result.sources:
        st.markdown("**Источники:**")
        for index, source in enumerate(result.sources, start=1):
            page = f", стр. {source.page}" if source.page is not None else ""
            st.markdown(
                f"{index}. **{source.title}** - {source.section or '-'}{page}"
            )
            if source.matched_excerpt:
                st.caption(source.matched_excerpt)
    else:
        st.info("В базе знаний не найдено релевантных источников.")

    with st.expander("Диагностика"):
        st.write(f"Модель: {result.model}")
        st.write(f"Prompt length: {result.prompt_length}")
        st.write(f"Elapsed seconds: {result.elapsed_seconds}")
        st.write(f"Chunks used: {result.chunks_used}")
        st.write(f"Retrieval limit: {result.retrieval_limit}")
        st.write(f"Candidate limit: {result.candidate_limit}")
        st.write(f"Context limit: {result.context_limit}")
        st.write(f"Context score ratio: {result.context_score_ratio}")
        if result.sources:
            st.write("Chunks:")
            for source in result.sources:
                st.write(
                    {
                        "title": source.title,
                        "section": source.section,
                        "logical_unit_title": source.logical_unit_title,
                        "page": source.page,
                        "score": source.score,
                    }
                )


if __name__ == "__main__":
    main()
