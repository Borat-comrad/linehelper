"""Experimental LLM query analyzer that returns a structured search plan."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from linehelper.llm.ollama_client import OllamaClient


DEFAULT_ANALYZER_MODEL = "qwen2.5:3b"
ANALYZER_NUM_PREDICT = 900

ALLOWED_INTENTS = frozenset(
    {
        "company_identity",
        "company_ckp",
        "org_structure",
        "roles_responsibility",
        "zrs_definition",
        "zrs_approval",
        "vacation",
        "document_flow",
        "contract_approval",
        "business_trip",
        "order_disposition",
        "task_management",
        "weekly_planning",
        "statistics_kpi",
        "onboarding_position",
        "written_communication",
        "equipment_it_request",
        "kp_commercial_offer",
        "ambiguous_abbreviation",
        "document_loss",
        "off_topic",
        "unknown",
    }
)

ALLOWED_ANSWER_TYPES = frozenset(
    {
        "definition",
        "procedure",
        "list",
        "comparison",
        "clarification",
        "partial_answer",
        "no_answer",
        "general",
    }
)

KNOWN_SOURCE_TITLES = frozenset(
    {
        "2026-03-03_Оргсхема _ Компании",
        "ИП-0002 Цели и замыслы компании Serviceline",
        "ИП-0003 ЦКП SERVICELINE",
        "ИП-0004 Структура ЗРС",
        "ИП-0005 Распоряжения",
        "ИП-0006 Документооборот",
        "Инструкция Согласования договоров в Документообороте",
        "Инструкция Согласования командировки в Документообороте",
        "Инструкция Согласования ЗРС в Документообороте",
        "Регламент по письменной коммуникации",
        "Регламент по планированию на неделю",
    }
)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_KP_RE = re.compile(r"(?<![0-9a-zа-яё])кп(?![0-9a-zа-яё])", re.IGNORECASE)
_FORBIDDEN_CKP_MEANING_RE = re.compile(
    r"центр\s+комплексных\s+предложений",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryPlan:
    intent: str
    normalized_question: str
    query_expansions: list[str]
    preferred_sources: list[str]
    answer_type: str
    needs_clarification: bool
    clarification_question: str | None
    confidence: float
    notes: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict for diagnostics and smoke scripts."""
        return asdict(self)


class AnalyzerChatClient(Protocol):
    model: str

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        """Return model text for analyzer messages."""


class QueryAnalyzer:
    """Build a diagnostic QueryPlan without changing the production RAG flow."""

    def __init__(
        self,
        ollama_client: AnalyzerChatClient | None = None,
        model: str | None = None,
    ) -> None:
        self.model = model or _load_analyzer_model()
        self.ollama_client = ollama_client or OllamaClient(
            model=self.model,
            temperature=0,
            num_predict=ANALYZER_NUM_PREDICT,
        )
        self.last_error: str | None = None

    def analyze(self, question: str) -> QueryPlan:
        """Analyze a question into a QueryPlan, falling back safely on errors."""
        clean_question = question.strip()
        if not clean_question:
            self.last_error = "empty question"
            return fallback_query_plan(question)

        messages = build_query_analyzer_prompt(clean_question)
        self.last_error = None

        try:
            content = self.ollama_client.chat(
                messages,
                model=self.model,
                temperature=0,
                num_predict=ANALYZER_NUM_PREDICT,
            )
            return parse_query_plan_response(content, clean_question)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return fallback_query_plan(clean_question)


def build_query_analyzer_prompt(question: str) -> list[dict[str, str]]:
    """Build strict chat messages for the JSON-only query analyzer."""
    system_prompt = f"""
Ты LLM Query Analyzer для локального корпоративного RAG LineHelper.
Твоя задача - НЕ отвечать пользователю, а вернуть один JSON-объект QueryPlan.

Правила ответа:
- Верни только JSON.
- Не используй Markdown.
- Не добавляй пояснения до или после JSON.
- Не отвечай на вопрос пользователя.
- Не выдумывай источники; preferred_sources заполняй только если источник очевиден из корпоративной карты.
- preferred_sources может содержать только эти known source titles: {", ".join(sorted(KNOWN_SOURCE_TITLES))}.
- Если подходящего source title нет в списке known source titles, верни preferred_sources=[].
- Верни ровно один JSON-объект.
- intent должен быть одним из: {", ".join(sorted(ALLOWED_INTENTS))}.
- answer_type должен быть одним из: {", ".join(sorted(ALLOWED_ANSWER_TYPES))}.
- Если вопрос вне корпоративной базы, используй intent="off_topic" и answer_type="no_answer".
- Если вопрос неоднозначный про "КП", используй intent="ambiguous_abbreviation", answer_type="clarification", needs_clarification=true.
- Если вопрос про "ЦКП", используй intent="company_ckp".
- ЦКП = ценный конечный продукт. ЦКП НЕ означает "центр комплексных предложений".

Корпоративная карта смыслов:
- ЦКП = ценный конечный продукт компании.
- КП без уточнения неоднозначно: может быть коммерческое предложение, но может путаться с ЦКП.
- ЗРС относится к документу "ИП-0004 Структура ЗРС".
- Вопросы про отделы, подразделения, отделения, оргструктуру, оргсхему, "из чего состоит компания" относятся к org_structure.
- Вопросы "кто отвечает", "какой отдел занимается", "функции отдела" относятся к roles_responsibility.
- Вопросы про отпуск относятся к vacation.
- Вопросы про договоры относятся к contract_approval.
- Вопросы про командировки относятся к business_trip.
- Вопросы про распоряжения относятся к order_disposition.
- Вопросы про задачи, взять задачу в работу, направить задачу относятся к task_management.
- Вопросы про потерю документа относятся к document_loss.
- Вопросы про ноутбук, оборудование, доступ, IT-заявку относятся к equipment_it_request.
- Вопросы "чем занимается компания?", "что делает компания?", "какая цель компании?" относятся к company_identity, а не к org_structure.
- Для document_loss не используй answer_type="procedure"; используй "partial_answer" или "no_answer".
- Для equipment_it_request не используй answer_type="procedure"; если нет известного источника, preferred_sources должен быть пустым.

JSON schema:
{{
  "intent": "org_structure",
  "normalized_question": "Какие подразделения и отделения есть в организационной структуре компании?",
  "query_expansions": [
    "оргсхема компании",
    "организационная структура компании",
    "крупные отделения компании",
    "подразделения компании",
    "отделы компании"
  ],
  "preferred_sources": [
    "2026-03-03_Оргсхема _ Компании"
  ],
  "answer_type": "list",
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.9,
  "notes": "Вопрос про организационную структуру компании."
}}
""".strip()
    user_prompt = f"Вопрос пользователя: {question}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_query_plan_response(content: str, question: str) -> QueryPlan:
    """Parse a possibly messy model response into a validated QueryPlan."""
    data = _loads_json_object(content)
    return _query_plan_from_mapping(data, question)


def fallback_query_plan(question: str) -> QueryPlan:
    """Small rule-based safety net for unavailable or malformed analyzer output."""
    clean_question = question.strip()
    normalized = _normalize_for_match(clean_question)

    if _is_ckp_question(normalized):
        return QueryPlan(
            intent="company_ckp",
            normalized_question="Что такое ЦКП как ценный конечный продукт компании?",
            query_expansions=[
                "ЦКП компании",
                "ценный конечный продукт компании",
                "ИП-0003 ЦКП SERVICELINE",
            ],
            preferred_sources=["ИП-0003 ЦКП SERVICELINE"],
            answer_type="definition",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.75,
            notes="Fallback: ЦКП зафиксирован как ценный конечный продукт.",
        )

    if _is_kp_commercial_offer_question(normalized):
        return QueryPlan(
            intent="kp_commercial_offer",
            normalized_question="Что известно про коммерческое предложение?",
            query_expansions=[
                "коммерческое предложение",
                "КП как коммерческое предложение",
                "отдел продаж коммерческое предложение",
            ],
            preferred_sources=[],
            answer_type="partial_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.7,
            notes="Fallback: КП явно уточнено как коммерческое предложение.",
        )

    if _is_ambiguous_kp_question(normalized):
        return QueryPlan(
            intent="ambiguous_abbreviation",
            normalized_question=clean_question,
            query_expansions=["КП", "коммерческое предложение", "ЦКП"],
            preferred_sources=[],
            answer_type="clarification",
            needs_clarification=True,
            clarification_question=(
                "Вы имеете в виду КП как коммерческое предложение или ЦКП "
                "как ценный конечный продукт компании?"
            ),
            confidence=0.8,
            notes="Fallback: аббревиатура КП без уточнения неоднозначна.",
        )

    if _contains_any(
        normalized,
        (
            "отделы",
            "отделов",
            "отделения",
            "отделений",
            "подразделения",
            "подразделений",
            "подразделен",
            "оргсхема",
            "оргструктура",
            "организационная структура",
            "структура компании",
            "из чего состоит компания",
            "устроена компания",
        ),
    ):
        return QueryPlan(
            intent="org_structure",
            normalized_question=(
                "Какие подразделения и отделения есть в организационной "
                "структуре компании?"
            ),
            query_expansions=[
                "оргсхема компании",
                "организационная структура компании",
                "подразделения компании",
                "отделы компании",
            ],
            preferred_sources=["2026-03-03_Оргсхема _ Компании"],
            answer_type="list",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.75,
            notes="Fallback: вопрос про организационную структуру.",
        )

    if _contains_any(
        normalized,
        ("кто отвечает", "какой отдел занимается", "функции отдела", "ответственный"),
    ):
        return _simple_plan(
            clean_question,
            intent="roles_responsibility",
            answer_type="list",
            query_expansions=["ответственность отделов", "функции подразделений"],
        )

    if _contains_any(normalized, ("отпуск", "отпуска", "отпускной")):
        return _simple_plan(
            clean_question,
            intent="vacation",
            answer_type="procedure",
            query_expansions=["оформить отпуск", "отпуск в документообороте"],
        )

    if "зрс" in normalized:
        if _contains_any(normalized, ("соглас", "утверд", "одобр", "подпис")):
            return _simple_plan(
                clean_question,
                intent="zrs_approval",
                answer_type="procedure",
                query_expansions=["согласование ЗРС", "ИП-0004 Структура ЗРС"],
                preferred_sources=["ИП-0004 Структура ЗРС"],
            )
        return _simple_plan(
            clean_question,
            intent="zrs_definition",
            answer_type="definition",
            query_expansions=["что такое ЗРС", "ИП-0004 Структура ЗРС"],
            preferred_sources=["ИП-0004 Структура ЗРС"],
        )

    if _contains_any(normalized, ("договор", "контракт")):
        return _simple_plan(
            clean_question,
            intent="contract_approval",
            answer_type="procedure",
            query_expansions=["согласование договора", "договор в документообороте"],
        )

    if _contains_any(normalized, ("командиров", "служебная поездка")):
        return _simple_plan(
            clean_question,
            intent="business_trip",
            answer_type="procedure",
            query_expansions=["согласование командировки", "оформить командировку"],
        )

    if _contains_any(normalized, ("распоряжение", "распоряжения")):
        return _simple_plan(
            clean_question,
            intent="order_disposition",
            answer_type="procedure",
            query_expansions=["распоряжения", "ИП-0005 Распоряжения"],
        )

    if _contains_any(
        normalized,
        ("задача", "задачу", "задачи", "взять задачу", "направить задачу"),
    ):
        return _simple_plan(
            clean_question,
            intent="task_management",
            answer_type="procedure",
            query_expansions=["работа с задачами", "взять задачу в работу"],
        )

    if _contains_any(
        normalized,
        (
            "потерял документ",
            "потеряла документ",
            "потерян документ",
            "утерян документ",
            "пропал документ",
        ),
    ):
        return QueryPlan(
            intent="document_loss",
            normalized_question="Что делать, если потерян документ?",
            query_expansions=["потерян документ", "утерян документ", "документ потеряли"],
            preferred_sources=[],
            answer_type="partial_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.7,
            notes="Fallback: вопрос про потерю документа.",
        )

    if _contains_any(
        normalized,
        ("ноутбук", "оборудование", "доступ", "it-заяв", "it заяв", "айти"),
    ):
        return _simple_plan(
            clean_question,
            intent="equipment_it_request",
            answer_type="general",
            query_expansions=["получить ноутбук", "заявка на оборудование", "IT-заявка"],
        )

    if _contains_any(
        normalized,
        (
            "чем занимается компания",
            "что делает компания",
            "о компании",
            "цель компании",
            "serviceline",
            "сервислайн",
        ),
    ):
        return _simple_plan(
            clean_question,
            intent="company_identity",
            answer_type="definition",
            query_expansions=["цели компании", "замыслы компании", "ЦКП Serviceline"],
            preferred_sources=[
                "ИП-0002 Цели и замыслы компании Serviceline",
                "ИП-0003 ЦКП SERVICELINE",
            ],
        )

    if _is_obvious_off_topic(normalized):
        return QueryPlan(
            intent="off_topic",
            normalized_question=clean_question,
            query_expansions=[],
            preferred_sources=[],
            answer_type="no_answer",
            needs_clarification=False,
            clarification_question=None,
            confidence=0.85,
            notes="Fallback: вопрос вне корпоративной базы знаний.",
        )

    return QueryPlan(
        intent="unknown",
        normalized_question=clean_question,
        query_expansions=[],
        preferred_sources=[],
        answer_type="general",
        needs_clarification=False,
        clarification_question=None,
        confidence=0.0,
        notes="Fallback: intent не определен.",
    )


def _query_plan_from_mapping(data: dict[str, Any], question: str) -> QueryPlan:
    intent = data.get("intent")
    if not isinstance(intent, str) or intent not in ALLOWED_INTENTS:
        intent = "unknown"

    answer_type = data.get("answer_type")
    if not isinstance(answer_type, str) or answer_type not in ALLOWED_ANSWER_TYPES:
        answer_type = "general"

    normalized_question = _coerce_str(data.get("normalized_question")) or question
    query_expansions = _coerce_str_list(data.get("query_expansions"))
    preferred_sources = _coerce_str_list(data.get("preferred_sources"))
    needs_clarification = _coerce_bool(data.get("needs_clarification"))
    clarification_question = _coerce_optional_str(data.get("clarification_question"))
    confidence = _coerce_confidence(data.get("confidence"))
    notes = _coerce_optional_str(data.get("notes"))

    if intent == "ambiguous_abbreviation":
        answer_type = "clarification"
        needs_clarification = True
        clarification_question = clarification_question or (
            "Вы имеете в виду КП как коммерческое предложение или ЦКП "
            "как ценный конечный продукт компании?"
        )

    if intent == "off_topic":
        answer_type = "no_answer"

    plan = QueryPlan(
        intent=intent,
        normalized_question=normalized_question,
        query_expansions=query_expansions,
        preferred_sources=preferred_sources,
        answer_type=answer_type,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        confidence=confidence,
        notes=notes,
    )
    return _sanitize_plan(plan, question)


def _sanitize_plan(plan: QueryPlan, question: str) -> QueryPlan:
    normalized_question = _normalize_for_match(question)

    if _is_ambiguous_kp_question(normalized_question):
        return fallback_query_plan(question)

    if _is_kp_commercial_offer_question(normalized_question):
        return fallback_query_plan(question)

    if _is_company_identity_question(normalized_question):
        return _with_known_sources(fallback_query_plan(question))

    if plan.intent == "company_ckp" or _is_ckp_question(normalized_question):
        return _sanitize_ckp_plan(plan)

    answer_type = plan.answer_type
    preferred_sources = _filter_known_sources(plan.preferred_sources)

    if plan.intent == "document_loss":
        if answer_type == "procedure":
            answer_type = "partial_answer"
        preferred_sources = []

    if plan.intent == "equipment_it_request":
        if answer_type == "procedure":
            answer_type = "general"
        preferred_sources = []

    return QueryPlan(
        intent=plan.intent,
        normalized_question=plan.normalized_question,
        query_expansions=plan.query_expansions,
        preferred_sources=preferred_sources,
        answer_type=answer_type,
        needs_clarification=plan.needs_clarification,
        clarification_question=plan.clarification_question,
        confidence=plan.confidence,
        notes=plan.notes,
    )


def _sanitize_ckp_plan(plan: QueryPlan) -> QueryPlan:
    """Force the corporate meaning of CKP and its known source title."""

    query_expansions = [
        _FORBIDDEN_CKP_MEANING_RE.sub("ценный конечный продукт", value)
        for value in plan.query_expansions
    ]
    if not any("ценный конечный продукт" in value.casefold() for value in query_expansions):
        query_expansions.append("ценный конечный продукт компании")

    preferred_sources = ["ИП-0003 ЦКП SERVICELINE"]

    return QueryPlan(
        intent="company_ckp",
        normalized_question=_FORBIDDEN_CKP_MEANING_RE.sub(
            "ценный конечный продукт",
            plan.normalized_question,
        ),
        query_expansions=query_expansions,
        preferred_sources=preferred_sources,
        answer_type=plan.answer_type if plan.answer_type != "general" else "definition",
        needs_clarification=False,
        clarification_question=None,
        confidence=plan.confidence,
        notes=(
            _FORBIDDEN_CKP_MEANING_RE.sub("ценный конечный продукт", plan.notes)
            if plan.notes is not None
            else plan.notes
        ),
    )


def _with_known_sources(plan: QueryPlan) -> QueryPlan:
    return QueryPlan(
        intent=plan.intent,
        normalized_question=plan.normalized_question,
        query_expansions=plan.query_expansions,
        preferred_sources=_filter_known_sources(plan.preferred_sources),
        answer_type=plan.answer_type,
        needs_clarification=plan.needs_clarification,
        clarification_question=plan.clarification_question,
        confidence=plan.confidence,
        notes=plan.notes,
    )


def _filter_known_sources(sources: list[str]) -> list[str]:
    return [source for source in sources if source in KNOWN_SOURCE_TITLES]


def _loads_json_object(content: str) -> dict[str, Any]:
    candidates = [content.strip()]

    block_match = _JSON_BLOCK_RE.search(content)
    if block_match:
        candidates.append(block_match.group(1).strip())

    extracted = _extract_first_json_object(content)
    if extracted:
        candidates.append(extracted)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    raise ValueError("model response does not contain a valid JSON object")


def _extract_first_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]

    return None


def _simple_plan(
    question: str,
    *,
    intent: str,
    answer_type: str,
    query_expansions: list[str],
    preferred_sources: list[str] | None = None,
) -> QueryPlan:
    return QueryPlan(
        intent=intent,
        normalized_question=question,
        query_expansions=query_expansions,
        preferred_sources=preferred_sources or [],
        answer_type=answer_type,
        needs_clarification=False,
        clarification_question=None,
        confidence=0.65,
        notes=f"Fallback: распознан intent {intent}.",
    )


def _load_analyzer_model() -> str:
    return (
        os.getenv("OLLAMA_ANALYZER_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or DEFAULT_ANALYZER_MODEL
    )


def _is_ckp_question(question: str) -> bool:
    return "цкп" in question or _contains_any(
        question,
        (
            "ценный конечный продукт",
            "ценного конечного продукта",
            "ценному конечному продукту",
            "ценным конечным продуктом",
        ),
    )


def _is_company_identity_question(question: str) -> bool:
    return _contains_any(
        question,
        (
            "чем занимается компания",
            "что делает компания",
            "какая цель компании",
            "цель компании",
            "о компании",
            "что такое serviceline",
            "что такое сервислайн",
        ),
    )


def _is_kp_commercial_offer_question(question: str) -> bool:
    return "коммерческое предложение" in question or (
        _KP_RE.search(question) is not None
        and _contains_any(
            question,
            (
                "коммерческого предложения",
                "коммерческому предложению",
                "коммерческим предложением",
                "коммерческих предложений",
            ),
        )
    )


def _is_ambiguous_kp_question(question: str) -> bool:
    return (
        _KP_RE.search(question) is not None
        and not _is_ckp_question(question)
        and not _is_kp_commercial_offer_question(question)
    )


def _is_obvious_off_topic(question: str) -> bool:
    return _contains_any(
        question,
        (
            "как приготовить",
            "рецепт",
            "борщ",
            "погода",
            "утят",
            "котлет",
            "фильм",
            "музыка",
        ),
    )


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _normalize_for_match(value: str) -> str:
    return value.casefold().replace("ё", "е")


def _coerce_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean_value = value.strip()
    return clean_value or None


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "да"}
    return bool(value)


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))
