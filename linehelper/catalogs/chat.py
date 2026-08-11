"""Deterministic exact catalog capability for the main LineHelper chat flow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
import re
from typing import Protocol

from .models import CatalogPartResult
from .search import CatalogSearch
from .store import CatalogStore


LOGGER = logging.getLogger(__name__)
MAX_RENDERED_OCCURRENCES = 20
DEFAULT_CATALOG_CANDIDATE_LIMIT = 5

_EXPLICIT_PATTERNS = (
    re.compile(r"^\s*найди\s+(?P<code>.+?)\s*[?!.]*$", re.IGNORECASE),
    re.compile(r"^\s*что\s+известно\s+о\s+(?P<code>.+?)\s*[?!.]*$", re.IGNORECASE),
    re.compile(r"^\s*где\s+используется\s+(?P<code>.+?)\s*[?!.]*$", re.IGNORECASE),
    re.compile(r"^\s*покажи(?:\s+информацию\s+по|\s+деталь)\s+(?P<code>.+?)\s*[?!.]*$", re.IGNORECASE),
    re.compile(r"^\s*(?:деталь|артикул|код\s+детали|part\s+number)\s*[:№]?\s*(?P<code>.+?)\s*[?!.]*$", re.IGNORECASE),
)
_BARE_CODE = re.compile(r"^[A-Z0-9][A-Z0-9 ._/-]{4,29}$", re.IGNORECASE)
_EXPLICIT_CODE = re.compile(r"^[A-Z0-9][A-Z0-9 ._/-]{3,39}$", re.IGNORECASE)
_UPPERCASE_UNKNOWN = re.compile(r"^[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9_./-]{3,39}$")
_TEXT_SEARCH_PATTERNS = (
    re.compile(
        r"^\s*найди\s+в\s+каталоге\b\s*:?\s*(?P<query>.*?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*поищи\s+в\s+каталоге\b\s*:?\s*(?P<query>.*?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*поиск\s+по\s+каталогу\b\s*:?\s*(?P<query>.*?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*найди\s+деталь\s+по\s+описанию\b\s*:?\s*(?P<query>.*?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*поищи\s+детали\b\s*:?\s*(?P<query>.*?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
)


class ExactCatalogSearch(Protocol):
    def find_part_by_number(self, part_number: str) -> list[CatalogPartResult]: ...


class CatalogSearchClient(ExactCatalogSearch, Protocol):
    def search_parts(
        self,
        query: str,
        limit: int = DEFAULT_CATALOG_CANDIDATE_LIMIT,
    ) -> list[CatalogPartResult]: ...


@dataclass(frozen=True)
class CatalogChatOutcome:
    identifier: str | None
    status: str
    answer: str
    results: tuple[CatalogPartResult, ...] = ()
    error: str | None = None
    route: str = "catalog_exact_lookup"
    search_query: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "identifier": self.identifier,
            "search_query": self.search_query,
            "status": self.status,
            "result_count": len(self.results),
            "results": [asdict(result) for result in self.results],
            "error": self.error,
        }


class CatalogChatService:
    """Recognize explicit part-code questions and call the existing exact search API."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        search: CatalogSearchClient | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else None
        self.search = search

    def lookup(self, question: str) -> CatalogChatOutcome | None:
        identifier = extract_part_number_lookup(question)
        search_query = None if identifier is not None else extract_catalog_text_search(question)
        if identifier is None and search_query is None:
            return None
        route = "catalog_exact_lookup" if identifier is not None else "catalog_text_search"
        if search_query == "":
            return CatalogChatOutcome(
                identifier=None,
                search_query="",
                route=route,
                status="clarification",
                answer=(
                    "Укажите название, признаки или ведущий фрагмент кода после "
                    "команды «Поиск по каталогу: ...»."
                ),
            )
        if self.search is None:
            if self.db_path is None or not self.db_path.is_file():
                LOGGER.warning("%s unavailable: database missing", route)
                return _unavailable(
                    identifier=identifier,
                    search_query=search_query,
                    route=route,
                    error="catalog_database_missing",
                )
            search: CatalogSearchClient = CatalogSearch(CatalogStore(self.db_path))
        else:
            search = self.search
        if identifier is None:
            return self._text_search(search_query or "", search)
        return self._exact_lookup(identifier, search)

    def _exact_lookup(
        self,
        identifier: str,
        search: ExactCatalogSearch,
    ) -> CatalogChatOutcome:
        LOGGER.info("Catalog exact lookup invoked: identifier=%s", identifier)
        try:
            results = tuple(search.find_part_by_number(identifier))
        except Exception as exc:
            LOGGER.warning("Catalog exact lookup failed: %s", type(exc).__name__)
            return _unavailable(
                identifier=identifier,
                route="catalog_exact_lookup",
                error=type(exc).__name__,
            )
        if not results:
            LOGGER.info("Catalog exact lookup not found: identifier=%s", identifier)
            return CatalogChatOutcome(
                identifier=identifier,
                status="not_found",
                answer=f"Точного совпадения по коду {identifier} в каталоге не найдено.",
            )
        LOGGER.info(
            "Catalog exact lookup completed: identifier=%s result_count=%d",
            identifier,
            len(results),
        )
        return CatalogChatOutcome(
            identifier=identifier,
            status="found",
            answer=format_catalog_answer(results),
            results=results,
        )

    def _text_search(
        self,
        query: str,
        search: CatalogSearchClient,
    ) -> CatalogChatOutcome:
        LOGGER.info("Catalog text search invoked: query=%s", query)
        try:
            results = tuple(
                search.search_parts(query, limit=DEFAULT_CATALOG_CANDIDATE_LIMIT)
            )[:DEFAULT_CATALOG_CANDIDATE_LIMIT]
        except Exception as exc:
            LOGGER.warning("Catalog text search failed: %s", type(exc).__name__)
            return _unavailable(
                search_query=query,
                route="catalog_text_search",
                error=type(exc).__name__,
            )
        if not results:
            LOGGER.info("Catalog text search returned no candidates: query=%s", query)
            return CatalogChatOutcome(
                identifier=None,
                search_query=query,
                route="catalog_text_search",
                status="no_candidates",
                answer=(
                    f"По запросу «{query}» в каталоге ничего не найдено. "
                    "Уточните название, признаки или ведущий фрагмент кода."
                ),
            )
        LOGGER.info(
            "Catalog text search completed: query=%s candidate_count=%d",
            query,
            len(results),
        )
        return CatalogChatOutcome(
            identifier=None,
            search_query=query,
            route="catalog_text_search",
            status="candidates",
            answer=format_catalog_candidates(query, results),
            results=results,
        )


def extract_part_number_lookup(question: str) -> str | None:
    """Return a candidate only for a bare code or an explicit catalog-like request."""
    clean = question.strip()
    if not clean:
        return None
    for pattern in _EXPLICIT_PATTERNS:
        match = pattern.fullmatch(clean)
        if match:
            candidate = _clean_candidate(match.group("code"))
            if _is_explicit_candidate(candidate):
                return candidate
            return None
    candidate = _clean_candidate(clean)
    if not _BARE_CODE.fullmatch(candidate):
        return None
    compact = re.sub(r"[ ._/-]", "", candidate)
    if compact.isdecimal():
        return candidate if len(compact) >= 8 else None
    return candidate if any(char.isdigit() for char in compact) and any(char.isalpha() for char in compact) else None


def extract_catalog_text_search(question: str) -> str | None:
    """Extract only the payload of an explicit catalog text-search command."""
    for pattern in _TEXT_SEARCH_PATTERNS:
        match = pattern.fullmatch(question)
        if match:
            return _clean_text_search_query(match.group("query"))
    return None


def format_catalog_answer(results: tuple[CatalogPartResult, ...]) -> str:
    first = results[0]
    lines = [f"Деталь: {first.part_number}"]
    if first.part_name:
        lines.append(f"Наименование: {first.part_name}")
    lines.append(f"Найдено вхождений: {len(results)}")
    for index, result in enumerate(results[:MAX_RENDERED_OCCURRENCES], 1):
        assembly = result.assembly_code
        if result.assembly_name:
            assembly += f" — {result.assembly_name}"
        lines.extend(("", f"{index}. Узел: {assembly}", f"   Позиция: {result.position}"))
        if result.quantity is not None:
            quantity = result.quantity + (f" {result.unit}" if result.unit else "")
            lines.append(f"   Количество: {quantity}")
        lines.append(f"   Страница BOM: {result.source_page}")
        if result.reference_page is not None:
            lines.append(f"   Связанная страница: {result.reference_page}")
    if len(results) > MAX_RENDERED_OCCURRENCES:
        lines.extend(("", f"Показаны первые {MAX_RENDERED_OCCURRENCES} из {len(results)} вхождений."))
    return "\n".join(lines)


def format_catalog_candidates(
    query: str,
    results: tuple[CatalogPartResult, ...],
) -> str:
    lines = [f"По запросу «{query}» показано кандидатов: {len(results)}."]
    for index, result in enumerate(results, 1):
        label = result.part_number
        if result.part_name:
            label += f" — {result.part_name}"
        lines.append(f"{index}. {label}")
    lines.extend(
        (
            "",
            "Это кандидаты полнотекстового поиска, а не подтверждённые exact-совпадения.",
            "Чтобы получить все вхождения и данные спецификации, отправьте: «Найди <код детали>».",
        )
    )
    return "\n".join(lines)


def _clean_candidate(value: str) -> str:
    return value.strip().strip("?!.:,;\"'«»").strip()


def _clean_text_search_query(value: str) -> str:
    return value.strip().strip("?!.:,;\"'«»").strip()


def _is_explicit_candidate(candidate: str) -> bool:
    if _EXPLICIT_CODE.fullmatch(candidate) and any(char.isdigit() for char in candidate):
        return True
    return bool(_UPPERCASE_UNKNOWN.fullmatch(candidate) and "_" in candidate)


def _unavailable(
    *,
    error: str,
    route: str,
    identifier: str | None = None,
    search_query: str | None = None,
) -> CatalogChatOutcome:
    if route == "catalog_text_search":
        answer = (
            "Каталог запасных частей сейчас недоступен. "
            f"Поиск кандидатов по запросу «{search_query or ''}» не выполнен."
        )
    else:
        answer = (
            "Каталог запасных частей сейчас недоступен. "
            f"Точное совпадение по коду {identifier} не проверено."
        )
    return CatalogChatOutcome(
        identifier=identifier,
        search_query=search_query,
        route=route,
        status="unavailable",
        answer=answer,
        error=error,
    )
