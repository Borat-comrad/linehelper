"""Streamlit chat UI for the local read-only RAG MVP."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from pathlib import Path

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.llm.answer_generator import RagAnswerError, RagAnswerGenerator  # noqa: E402
from linehelper.ui.components import badge_html, source_card_html  # noqa: E402
from linehelper.ui.styles import APP_CSS  # noqa: E402


DB_PATH = PROJECT_ROOT / "data" / "memory" / "linehelper_memory.db"

WORK_MODES = (
    "Корпоративная справка",
    "Коммерческое предложение",
    "Поиск в 1С",
    "Диагностика памяти",
)

EXAMPLE_QUERIES = (
    "Что такое ЦКП компании?",
    "Как правильно оформить ЗРС?",
    "Какие правила документооборота действуют?",
    "Сформируй черновик КП по заявке",
)


@dataclass(frozen=True)
class UiSettings:
    mode: str
    retrieval_limit: int
    candidate_limit: int
    show_technical_details: bool


def main() -> None:
    st.set_page_config(page_title="LineHelper", page_icon="LH", layout="wide")
    st.markdown(APP_CSS, unsafe_allow_html=True)
    _init_session_state()

    settings = _render_sidebar()
    _render_header()

    if not DB_PATH.exists():
        _render_missing_db_error()
        return

    if "generator" not in st.session_state:
        st.session_state.generator = RagAnswerGenerator(db_path=DB_PATH)

    quick_question = _render_quick_scenarios()
    _render_chat_history(settings)

    placeholder = _chat_placeholder(settings.mode)
    typed_question = st.chat_input(placeholder)
    question = quick_question or typed_question
    if not question:
        return

    _handle_question(question, settings)


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _render_sidebar() -> UiSettings:
    with st.sidebar:
        st.markdown("## LineHelper")
        st.caption("Локальный контур Serviceline")

        st.markdown("### Режим работы")
        mode = st.radio(
            "Выберите сценарий",
            WORK_MODES,
            index=0,
            label_visibility="collapsed",
        )

        st.markdown("### Параметры поиска")
        retrieval_limit = st.slider("Limit источников", 1, 10, 5)
        candidate_limit = st.slider(
            "Candidate limit",
            min_value=max(retrieval_limit, 5),
            max_value=50,
            value=min(50, max(30, retrieval_limit * 6)),
        )
        show_technical_details = st.checkbox(
            "Показывать технические детали",
            value=False,
        )
        st.caption(
            "Веса semantic/episodic пока не настраиваются: текущий MVP читает "
            "semantic memory, а UI уже готов различать namespaces."
        )

        st.markdown("### Статус")
        db_status = "готова" if DB_PATH.exists() else "не найдена"
        db_class = "lh-status-ok" if DB_PATH.exists() else "lh-status-warn"
        st.markdown(
            f"""
<div class="lh-sidebar-card">
  <div class="lh-status-line"><span>Локальная LLM</span><span class="lh-status-ok">Ollama</span></div>
  <div class="lh-status-line"><span>База памяти</span><span class="{db_class}">{db_status}</span></div>
  <div class="lh-status-line"><span>Внешние API</span><span class="lh-status-ok">не используются</span></div>
</div>
""",
            unsafe_allow_html=True,
        )

    return UiSettings(
        mode=mode,
        retrieval_limit=retrieval_limit,
        candidate_limit=candidate_limit,
        show_technical_details=show_technical_details,
    )


def _render_header() -> None:
    badges = " ".join(
        [
            badge_html("semantic", "semantic"),
            badge_html("episodic", "episodic"),
            badge_html("1C context", "onec"),
            badge_html("local only", "local"),
        ]
    )
    st.markdown(
        f"""
<section class="lh-hero">
  <h1>LineHelper — локальный помощник Serviceline</h1>
  <p>Поиск по корпоративной базе знаний, помощь с КП и работа с локальной памятью</p>
  <div class="lh-badge-row">{badges}</div>
</section>
""",
        unsafe_allow_html=True,
    )


def _render_missing_db_error() -> None:
    st.error(
        "База semantic memory не найдена. Сначала пересоберите "
        "data/memory/linehelper_memory.db из curated chunks."
    )


def _render_quick_scenarios() -> str | None:
    st.markdown('<div class="lh-section-title">Быстрые сценарии</div>', unsafe_allow_html=True)
    columns = st.columns(4)
    selected_query: str | None = None
    for index, query in enumerate(EXAMPLE_QUERIES):
        with columns[index]:
            if st.button(query, key=f"quick_query_{index}", use_container_width=True):
                selected_query = query
    st.markdown(
        '<div class="lh-hint">Можно начать с готового запроса или задать свой вопрос в чате ниже.</div>',
        unsafe_allow_html=True,
    )
    return selected_query


def _render_chat_history(settings: UiSettings) -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant" and message.get("result"):
                _render_result_details(message["result"], settings)


def _handle_question(question: str, settings: UiSettings) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Ищу источники и спрашиваю локальную модель..."):
                result = st.session_state.generator.answer(
                    question,
                    retrieval_limit=settings.retrieval_limit,
                    candidate_limit=settings.candidate_limit,
                )
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
        _render_result_details(result, settings)

    st.session_state.messages.append(
        {"role": "assistant", "content": result.answer, "result": result}
    )


def _chat_placeholder(mode: str) -> str:
    placeholders = {
        "Корпоративная справка": "Задайте вопрос по корпоративной базе знаний",
        "Коммерческое предложение": "Опишите заявку или попросите черновик КП",
        "Поиск в 1С": "Спросите про контекст 1С или документооборот",
        "Диагностика памяти": "Проверьте, какие источники находятся по вопросу",
    }
    return placeholders.get(mode, placeholders["Корпоративная справка"])


def _render_result_details(result, settings: UiSettings) -> None:
    source_count = len(result.sources)
    with st.expander(f"Источники ответа ({source_count})", expanded=source_count > 0):
        if result.sources:
            for index, source in enumerate(result.sources, start=1):
                st.markdown(
                    source_card_html(source, index=index),
                    unsafe_allow_html=True,
                )
        elif result.response_kind == "no_answer":
            st.info("Релевантные источники ответа не найдены.")
        else:
            st.info("Источники ответа не использовались.")

    if not settings.show_technical_details:
        return

    with st.expander("Служебная информация", expanded=False):
        st.write(
            {
                "mode": settings.mode,
                "model": result.model,
                "prompt_length": result.prompt_length,
                "elapsed_seconds": result.elapsed_seconds,
                "chunks_used": result.chunks_used,
                "retrieval_limit": result.retrieval_limit,
                "candidate_limit": result.candidate_limit,
                "context_limit": result.context_limit,
                "context_score_ratio": result.context_score_ratio,
                "response_kind": result.response_kind,
            }
        )
        if result.diagnostic_candidates:
            st.markdown("**Диагностические кандидаты, не использованные в ответе:**")
            for index, source in enumerate(result.diagnostic_candidates, start=1):
                st.markdown(
                    source_card_html(source, index=index),
                    unsafe_allow_html=True,
                )


if __name__ == "__main__":
    main()
