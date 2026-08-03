from __future__ import annotations

from linehelper.analytics.config import AnalyticsConfig
from linehelper.analytics.interaction_logger import InteractionLogger
from linehelper.analytics.interaction_store import InteractionStore
from linehelper.analytics.models import InteractionFeedback
from linehelper.llm.answer_generator import RagAnswerGenerator
from linehelper.rag.query_analyzer import fallback_query_plan
from linehelper.ui.streamlit_app import submit_interaction_feedback


def test_successful_rag_answer_is_logged_with_interaction_id(tmp_path) -> None:
    store = InteractionStore(tmp_path / "analytics.db")
    logger = InteractionLogger(config=_config(tmp_path / "analytics.db"), store=store)
    generator = RagAnswerGenerator(
        retriever=FakeRetriever(),
        llm_client=FakeClient(),
        query_analyzer=FakeAnalyzer(),
        interaction_logger=logger,
    )

    result = generator.answer("Нет ли ответа?", session_id="session-1")
    logger.flush()
    rows = store.fetch_rows("interactions")
    logger.close()

    assert result.interaction_id
    assert result.analytics_logged is True
    assert result.response_kind == "no_answer"
    assert rows[0]["interaction_id"] == result.interaction_id
    assert rows[0]["original_question"] == "Нет ли ответа?"


def test_logger_exception_does_not_change_rag_answer() -> None:
    generator = RagAnswerGenerator(
        retriever=FakeRetriever(),
        llm_client=FakeClient(),
        query_analyzer=FakeAnalyzer(),
        interaction_logger=ExplodingLogger(),
    )

    result = generator.answer("Нет ли ответа?")

    assert result.response_kind == "no_answer"
    assert "недостаточно данных" in result.answer
    assert result.interaction_id is None
    assert result.analytics_logged is False
    assert result.analytics_error == "RuntimeError"
    assert generator.llm_client.calls == 0


def test_ui_feedback_helper_persists_positive_and_negative_events(tmp_path) -> None:
    store = InteractionStore(tmp_path / "analytics.db")
    logger = InteractionLogger(config=_config(tmp_path / "analytics.db"), store=store)
    generator = RagAnswerGenerator(
        retriever=FakeRetriever(),
        llm_client=FakeClient(),
        query_analyzer=FakeAnalyzer(),
        interaction_logger=logger,
    )
    result = generator.answer("Тест", session_id="ui-session")
    assert result.interaction_id is not None

    positive = submit_interaction_feedback(
        logger,
        interaction_id=result.interaction_id,
        rating="positive",
    )
    negative = submit_interaction_feedback(
        logger,
        interaction_id=result.interaction_id,
        rating="negative",
        reason="wrong_sources",
        comment="token=secret manager@example.com",
    )
    logger.flush()
    rows = store.fetch_rows("interaction_feedback")
    logger.close()

    assert positive is True and negative is True
    assert [row["rating"] for row in rows] == ["positive", "negative"]
    assert rows[1]["reason"] == "wrong_sources"
    assert "secret" not in rows[1]["comment"]
    assert "manager@example.com" not in rows[1]["comment"]


class FakeRetriever:
    def retrieve(self, question: str, *, limit: int, candidate_limit: int):
        return []


class FakeClient:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return "unused"


class FakeAnalyzer:
    def analyze(self, question: str):
        return fallback_query_plan(question)


class ExplodingLogger:
    last_error = None

    def record_answer(self, result, **kwargs):
        raise RuntimeError("password=must-not-leak")


def _config(db_path) -> AnalyticsConfig:
    return AnalyticsConfig(
        enabled=True,
        db_path=db_path,
        retention_days=90,
        store_text=True,
        redact_pii=True,
        queue_size=20,
        user_hash_secret=None,
    )
