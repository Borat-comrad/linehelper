"""Deterministic exact catalog capability for the main LineHelper chat flow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
import re
from typing import Protocol

from .models import (
    CatalogAssemblyResult,
    CatalogCodeSearchResult,
    CatalogPartResult,
    CatalogProbeDiagnostics,
    CatalogSource,
    SourceRequirement,
)
from .navigation import CatalogOccurrenceNavigation, CatalogPageLocator
from .presentation import format_catalog_quantity
from .search import CatalogSearch, MIN_CODE_SUBSTRING_LENGTH, is_part_number_like
from .store import CatalogStore


LOGGER = logging.getLogger(__name__)
MAX_RENDERED_OCCURRENCES = 20
DEFAULT_CATALOG_CANDIDATE_LIMIT = 5
MIN_NATURAL_PROBE_TERMS = 1
MAX_NATURAL_PROBE_TERMS = 6
MIN_NATURAL_PROBE_TERM_LENGTH = 3
MIN_NATURAL_PROBE_FIELD_COVERAGE = 0.75
MIN_NATURAL_PROBE_COHERENT_RESULTS = 2

_NATURAL_CATALOG_STOP_WORDS = frozenset(
    {
        "что", "ты", "знаешь", "про", "найди", "покажи", "какие", "есть",
        "детали", "деталь", "по", "это", "мне", "информация", "информацию",
        "известно", "все", "всё", "для", "на", "об", "о", "перечисли",
        "комплектующие", "подскажи", "подскажите", "пожалуйста", "можешь",
        "поищи", "подбери", "варианты", "входит", "входят", "находится",
        "находятся", "установлен", "установлена", "установлено", "стоит",
        "проверь", "каталог", "нужны", "входящие", "который", "которая",
        "которое", "которые", "там", "какой", "какая", "какое",
    }
)
_CORPORATE_ROUTE_TERMS = frozenset(
    {
        "кто", "отвечает", "руководитель", "сотрудник", "документооборот",
        "регламент", "инструкция", "распоряжение", "устно", "письменно",
        "согласование", "договор", "командировка", "отпуск", "зрс", "цкп",
        "компания", "serviceline", "процедура", "правило", "правила",
        "должность", "должности", "должностей",
    }
)
_MIXED_ROUTE_TERMS = frozenset(
    {
        "обслуживание", "обслуживать", "обслужить", "ремонт", "ремонтировать", "замена",
        "заменить", "настройка", "настроить", "процедура", "инструкция",
        "порядок", "правило", "правила", "калибровка", "калибровки",
    }
)
_PROCEDURAL_QUERY_TOKENS = frozenset(
    {
        "как", "его", "ее", "её", "их", "обслуживать", "обслужить",
        "обслуживание", "ремонтировать", "ремонт", "заменить", "замена",
        "настроить", "настройка", "процедура", "инструкция",
        "порядок", "правило", "правила", "калибровка", "калибровки",
    }
)
_PROCEDURAL_TERM_STEMS = (
    "обслуж",
    "ремонт",
    "замен",
    "настро",
    "процедур",
    "инструкц",
    "калибров",
    "правил",
)
_NATURAL_CODE_PATTERNS = (
    re.compile(
        r"\b(?:с\s+)?начал\w*\s+кода\s+(?P<code>[A-ZА-ЯЁ]?\s*[- ._/]?\s*\d[A-ZА-ЯЁ0-9 ._/-]{3,29})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:в\s+)?код\w*\s+(?:котор\w+\s+)?(?:встречается|содержится|есть)\s+"
        r"(?P<code>[A-ZА-ЯЁ]?\s*[- ._/]?\s*\d[A-ZА-ЯЁ0-9 ._/-]{3,29})",
        re.IGNORECASE,
    ),
)
_NATURAL_ENTITY_CONTEXT_PATTERNS = (
    re.compile(
        r"^\s*есть\s+ли(?:\s+в\s+каталоге)?\s+(?P<entity>.+?)\s+для\s+(?P<context>.+?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*покажи\s+(?P<entity>.+?)\s+(?:в|для)\s+(?P<context>.+?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:а\s+)?(?:подбери|найди|покажи)?\s*(?P<entity>.+?),?\s+"
        r"котор\w*\s+(?:установлен\w*|сто\w*|наход\w*)\s+"
        r"(?:в|на)\s+(?P<context>.+?)(?:\s+можешь\s+найти)?\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:а\s+)?(?P<entity>.+?)\s+котор\w*\s+"
        r"(?P<context>(?:внизу|снизу|наверху|сверху)\s+.+?)"
        r"(?:\s+можешь\s+найти)?\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:подскажи(?:те)?\s*,?\s*)?(?:пожалуйста\s*[:,]?\s*)?"
        r"(?P<entity>.+?)\s+(?:в|для|на)\s+(?P<context>.+?)\s+есть\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*по\s+(?P<context>.+?)\s*:\s*(?P<entity>.+?)\s+там\s+"
        r"(?:како\w+\s+)?(?:стоит|находится)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:а\s+)?(?P<entity>.+?)\s+(?:в|для|на)\s+"
        r"(?P<context>.+?)(?:\s+можешь\s+найти)?\s*[?!.]*$",
        re.IGNORECASE,
    ),
)
_NATURAL_SUBJECT_PATTERNS = (
    re.compile(
        r"^\s*(?:какие\s+детали\s+входят\s+в|что\s+входит\s+в|найди\s+детали|"
        r"покажи\s+что\s+есть\s+по|что\s+есть\s+по|что\s+стоит\s+в|"
        r"что\s+есть\s+в|что\s+находится\s+в|что\s+установлено\s+в|"
        r"что\s+ты\s+знаешь\s+про|что\s+известно\s+про|"
        r"перечисли\s+(?:комплектующ\w*|детал\w*)|"
        r"покажи\s+(?:состав|комплектующ\w*|детал\w*)|"
        r"проверь\s+каталог\s+на)\s+(?P<subject>.+?)\s*[?!.]*$",
        re.IGNORECASE,
    ),
)

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

_STRUCTURED_CODE = r"[A-Z0-9][A-Z0-9 ._/-]{3,39}"
_STRUCTURED_POSITION = r"[A-Z0-9][A-Z0-9._/-]{0,19}"
_BOM_POSITION_PATTERNS = (
    re.compile(
        rf"^\s*позиция\s+(?P<position>{_STRUCTURED_POSITION})\s+узла?\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\s*что\s+стоит\s+в\s+позиции\s+(?P<position>{_STRUCTURED_POSITION})\s+узла?\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\s*узел\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s+позиция\s+(?P<position>{_STRUCTURED_POSITION})\s*[?!.]*$",
        re.IGNORECASE,
    ),
)
_ASSEMBLY_CONTENT_PATTERNS = (
    re.compile(
        rf"^\s*(?:состав\s+узла|что\s+входит\s+в\s+узел|какие\s+детали\s+входят\s+в\s+узел|покажи\s+спецификацию\s+узла)\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\s*(?:мне\s+)?(?:нужны|покажи)\s+детал\w*\s*,?\s*"
        rf"(?:входящ\w*\s+)?в\s+узел\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
)
_ASSEMBLY_EXACT_PATTERNS = (
    re.compile(
        rf"^\s*(?:узел|покажи\s+узел)\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
)
_PART_EXACT_PATTERNS = (
    re.compile(
        rf"^\s*(?:деталь|код\s+детали)\s*:?[ ]*(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\s*покажи\s+(?:деталь\s+)?(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\s*где\s+используется\s+(?P<code>{_STRUCTURED_CODE})\s*[?!.]*$",
        re.IGNORECASE,
    ),
)


class ExactCatalogSearch(Protocol):
    def find_part_by_number(self, part_number: str) -> list[CatalogPartResult]: ...


class CatalogSearchClient(ExactCatalogSearch, Protocol):
    def search_part_number_candidates(
        self,
        query: str,
        limit: int = DEFAULT_CATALOG_CANDIDATE_LIMIT,
    ) -> CatalogCodeSearchResult: ...

    def search_parts(
        self,
        query: str,
        limit: int = DEFAULT_CATALOG_CANDIDATE_LIMIT,
    ) -> list[CatalogPartResult]: ...

    def find_assemblies_by_code(
        self,
        assembly_code: str,
    ) -> list[CatalogAssemblyResult]: ...

    def find_assembly_parts(self, assembly_code: str) -> list[CatalogPartResult]: ...

    def find_assembly_position(
        self,
        assembly_code: str,
        position: str,
    ) -> list[CatalogPartResult]: ...


@dataclass(frozen=True)
class StructuredCatalogIntent:
    kind: str
    code: str
    position: str | None = None


@dataclass(frozen=True)
class NaturalCatalogQuery:
    """Deterministically extracted catalog subject and optional assembly context."""

    query: str
    subject: str
    entity_terms: tuple[str, ...] = ()
    assembly_context: str | None = None
    mixed_signal: bool = False
    catalog_clause: str | None = None
    procedure_clause: str | None = None
    resolved_referent: str | None = None
    understanding_pattern: str | None = None
    normalization_applied: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogChatOutcome:
    identifier: str | None
    status: str
    answer: str
    results: tuple[CatalogPartResult, ...] = ()
    error: str | None = None
    route: str = "catalog_exact_lookup"
    search_query: str | None = None
    match_type: str | None = None
    catalog_sources: tuple[CatalogSource, ...] = ()
    navigation: tuple[CatalogOccurrenceNavigation, ...] = ()
    assemblies: tuple[CatalogAssemblyResult, ...] = ()
    position: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "identifier": self.identifier,
            "search_query": self.search_query,
            "status": self.status,
            "match_type": self.match_type,
            "result_count": len(self.results),
            "results": [asdict(result) for result in self.results],
            "catalog_sources": [item.to_dict() for item in self.catalog_sources],
            "navigation": [item.to_dict() for item in self.navigation],
            "assemblies": [item.to_dict() for item in self.assemblies],
            "assembly_count": len(self.assemblies),
            "position": self.position,
            "error": self.error,
        }


class CatalogChatService:
    """Route deterministic exact, code-candidate and catalog text searches."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        search: CatalogSearchClient | None = None,
        source_root: Path | str | None = None,
        page_locator: CatalogPageLocator | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else None
        self.search = search
        self.page_locator = page_locator or (
            CatalogPageLocator(source_root) if source_root is not None else None
        )

    def lookup(self, question: str) -> CatalogChatOutcome | None:
        structured_intent = extract_structured_catalog_intent(question)
        identifier = (
            None
            if structured_intent is not None
            else extract_part_number_lookup(question)
        )
        search_query = None if identifier is not None else extract_catalog_text_search(question)
        if structured_intent is None and identifier is None and search_query is None:
            return None
        route = (
            _structured_route(structured_intent.kind)
            if structured_intent is not None
            else "catalog_exact_lookup"
            if identifier is not None
            else "catalog_text_search"
        )
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
                    identifier=(
                        structured_intent.code
                        if structured_intent is not None
                        else identifier
                    ),
                    search_query=search_query,
                    route=route,
                    error="catalog_database_missing",
                )
            search: CatalogSearchClient = CatalogSearch(CatalogStore(self.db_path))
        else:
            search = self.search
        if structured_intent is not None:
            return self._structured_lookup(structured_intent, search)
        if identifier is not None and _is_bare_code_question(question, identifier):
            return self._bare_entity_lookup(identifier, search)
        if identifier is not None:
            return self._code_lookup(identifier, search, explicit_text_search=False)
        query = search_query or ""
        if is_part_number_like(query):
            return self._code_lookup(query, search, explicit_text_search=True)
        return self._text_search(query, search)

    def _structured_lookup(
        self,
        intent: StructuredCatalogIntent,
        search: CatalogSearchClient,
    ) -> CatalogChatOutcome:
        if intent.kind == "part_exact":
            return self._part_exact_lookup(intent.code, search)
        if intent.kind == "bom_position":
            return self._assembly_position_lookup(
                intent.code,
                intent.position or "",
                search,
            )
        return self._assembly_lookup(
            intent.code,
            search,
            contents=intent.kind == "assembly_contents",
        )

    def _part_exact_lookup(
        self,
        identifier: str,
        search: CatalogSearchClient,
    ) -> CatalogChatOutcome:
        try:
            results = tuple(search.find_part_by_number(identifier))
        except Exception as exc:
            return _unavailable(
                identifier=identifier,
                route="catalog_part_exact",
                error=type(exc).__name__,
            )
        if not results:
            return CatalogChatOutcome(
                identifier=identifier,
                route="catalog_part_exact",
                status="not_found",
                match_type="catalog_part_exact",
                answer=f"Деталь с кодом {identifier} в Catalog Store не найдена.",
            )
        navigation = self._navigation_for(results)
        return CatalogChatOutcome(
            identifier=identifier,
            route="catalog_part_exact",
            status="found",
            match_type="catalog_part_exact",
            answer=format_catalog_answer(results, navigation=navigation),
            results=results,
            catalog_sources=_catalog_sources_for(results),
            navigation=navigation,
        )

    def _assembly_lookup(
        self,
        assembly_code: str,
        search: CatalogSearchClient,
        *,
        contents: bool,
    ) -> CatalogChatOutcome:
        route = "catalog_assembly_contents" if contents else "catalog_assembly_exact"
        find_assemblies = getattr(search, "find_assemblies_by_code", None)
        find_parts = getattr(search, "find_assembly_parts", None)
        if not callable(find_assemblies) or not callable(find_parts):
            return _unavailable(
                identifier=assembly_code,
                route=route,
                error="assembly_search_unavailable",
            )
        try:
            assemblies = tuple(find_assemblies(assembly_code))
            results = tuple(find_parts(assembly_code)) if assemblies else ()
        except Exception as exc:
            return _unavailable(
                identifier=assembly_code,
                route=route,
                error=type(exc).__name__,
            )
        if not assemblies:
            return CatalogChatOutcome(
                identifier=assembly_code,
                route=route,
                status="not_found",
                match_type=route,
                answer=f"Узел с кодом {assembly_code} в Catalog Store не найден.",
            )
        navigation = self._navigation_for(results)
        return CatalogChatOutcome(
            identifier=assembly_code,
            route=route,
            status="found",
            match_type=route,
            answer=format_assembly_contents(assemblies, results),
            results=results,
            catalog_sources=_catalog_sources_for(results),
            navigation=navigation,
            assemblies=assemblies,
        )

    def _assembly_position_lookup(
        self,
        assembly_code: str,
        position: str,
        search: CatalogSearchClient,
    ) -> CatalogChatOutcome:
        find_assemblies = getattr(search, "find_assemblies_by_code", None)
        find_position = getattr(search, "find_assembly_position", None)
        if not callable(find_assemblies) or not callable(find_position):
            return _unavailable(
                identifier=assembly_code,
                route="catalog_bom_position",
                error="assembly_position_search_unavailable",
            )
        try:
            assemblies = tuple(find_assemblies(assembly_code))
            results = tuple(find_position(assembly_code, position)) if assemblies else ()
        except Exception as exc:
            return _unavailable(
                identifier=assembly_code,
                route="catalog_bom_position",
                error=type(exc).__name__,
            )
        if not assemblies:
            answer = f"Узел с кодом {assembly_code} в Catalog Store не найден."
        elif not results:
            answer = f"В узле {assembly_code} позиция {position} не найдена."
        else:
            answer = format_bom_position(assemblies, results, position=position)
        navigation = self._navigation_for(results)
        return CatalogChatOutcome(
            identifier=assembly_code,
            route="catalog_bom_position",
            status="found" if results else "not_found",
            match_type="catalog_bom_position",
            answer=answer,
            results=results,
            catalog_sources=_catalog_sources_for(results),
            navigation=navigation,
            assemblies=assemblies,
            position=position,
        )

    def _bare_entity_lookup(
        self,
        identifier: str,
        search: CatalogSearchClient,
    ) -> CatalogChatOutcome:
        find_assemblies = getattr(search, "find_assemblies_by_code", None)
        if not callable(find_assemblies):
            return self._code_lookup(identifier, search, explicit_text_search=False)
        try:
            assemblies = tuple(find_assemblies(identifier))
            part_results = tuple(search.find_part_by_number(identifier))
        except Exception as exc:
            return _unavailable(
                identifier=identifier,
                route="catalog_entity_lookup",
                error=type(exc).__name__,
            )
        if assemblies and part_results:
            return CatalogChatOutcome(
                identifier=identifier,
                route="catalog_entity_ambiguity",
                status="ambiguous",
                match_type="catalog_entity_ambiguous",
                answer=format_catalog_entity_ambiguity(
                    identifier,
                    assemblies,
                    part_results,
                ),
                results=part_results,
                catalog_sources=_catalog_sources_for(part_results),
                assemblies=assemblies,
            )
        if assemblies:
            return self._assembly_lookup(identifier, search, contents=False)
        if part_results:
            navigation = self._navigation_for(part_results)
            return CatalogChatOutcome(
                identifier=identifier,
                route="catalog_exact_lookup",
                status="found",
                match_type=_exact_match_type(identifier, part_results),
                answer=format_catalog_answer(part_results, navigation=navigation),
                results=part_results,
                catalog_sources=_catalog_sources_for(part_results),
                navigation=navigation,
            )
        return self._code_lookup(identifier, search, explicit_text_search=False)

    def probe_natural_language(
        self,
        question: str,
    ) -> CatalogProbeDiagnostics:
        """Probe catalog FTS without converting weak matches into a route."""
        natural_query = _extract_natural_catalog_query(question)
        if natural_query is None:
            question_terms = _catalog_terms(question)
            mixed_signal = (
                _contains_procedural_signal(question_terms)
                or ("делать" in question_terms and "надо" in question_terms)
            ) and _has_catalog_language_signal(question)
            return _empty_probe(
                question,
                performed=False,
                mixed_signal=mixed_signal,
            )
        query = natural_query.query
        mixed_signal = natural_query.mixed_signal
        if self.search is None:
            if self.db_path is None or not self.db_path.is_file():
                return _empty_probe(
                    query,
                    performed=False,
                    mixed_signal=mixed_signal,
                    natural_query=natural_query,
                )
            search: CatalogSearchClient = CatalogSearch(CatalogStore(self.db_path))
        else:
            search = self.search
        try:
            results = _natural_probe_candidates(
                search,
                query,
                entity_terms=natural_query.entity_terms,
                assembly_context=natural_query.assembly_context,
            )
        except Exception as exc:
            LOGGER.warning("Catalog natural-language probe failed: %s", type(exc).__name__)
            return _empty_probe(
                query,
                performed=True,
                mixed_signal=mixed_signal,
                natural_query=natural_query,
            )
        if not results:
            return _empty_probe(
                query,
                performed=True,
                mixed_signal=mixed_signal,
                natural_query=natural_query,
            )
        terms = _catalog_terms(query)
        coverages = tuple(_result_field_coverage(result, terms) for result in results)
        top_coverage = coverages[0]
        coherent_count = sum(
            coverage >= MIN_NATURAL_PROBE_FIELD_COVERAGE for coverage in coverages
        )
        strong = (
            top_coverage >= MIN_NATURAL_PROBE_FIELD_COVERAGE
            and (
                coherent_count >= MIN_NATURAL_PROBE_COHERENT_RESULTS
                or (len(terms) >= 2 and top_coverage == 1.0)
            )
        )
        if not strong:
            return CatalogProbeDiagnostics(
                performed=True,
                query=query,
                result_count=len(results),
                top_score=results[0].search_score,
                top_field_coverage=top_coverage,
                coherent_result_count=coherent_count,
                source_route="mixed" if mixed_signal else "corporate",
                outcome=None,
                resolved_requirements=_source_requirements(
                    natural_query.subject,
                    mixed_signal=mixed_signal,
                    catalog_status="not_found",
                ),
                catalog_subject=natural_query.subject,
                catalog_entity_terms=natural_query.entity_terms,
                catalog_assembly_context=natural_query.assembly_context,
                catalog_clause=natural_query.catalog_clause,
                procedure_clause=natural_query.procedure_clause,
                resolved_referent=natural_query.resolved_referent,
                understanding_pattern=natural_query.understanding_pattern,
                normalization_applied=natural_query.normalization_applied,
            )
        outcome = CatalogChatOutcome(
            identifier=None,
            search_query=query,
            route="catalog_natural_search",
            status="candidates",
            match_type="catalog_fts",
            answer=format_natural_catalog_candidates(query, results),
            results=results,
            catalog_sources=_catalog_sources_for(results),
        )
        return CatalogProbeDiagnostics(
            performed=True,
            query=query,
            result_count=len(results),
            top_score=results[0].search_score,
            top_field_coverage=top_coverage,
            coherent_result_count=coherent_count,
            source_route="mixed" if mixed_signal else "catalog",
            outcome=outcome,
            resolved_requirements=_source_requirements(
                natural_query.subject,
                mixed_signal=mixed_signal,
                catalog_status="found",
            ),
            catalog_subject=natural_query.subject,
            catalog_entity_terms=natural_query.entity_terms,
            catalog_assembly_context=natural_query.assembly_context,
            catalog_clause=natural_query.catalog_clause,
            procedure_clause=natural_query.procedure_clause,
            resolved_referent=natural_query.resolved_referent,
            understanding_pattern=natural_query.understanding_pattern,
            normalization_applied=natural_query.normalization_applied,
        )

    def _code_lookup(
        self,
        identifier: str,
        search: CatalogSearchClient,
        *,
        explicit_text_search: bool,
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
        if results:
            LOGGER.info(
                "Catalog exact lookup completed: identifier=%s result_count=%d",
                identifier,
                len(results),
            )
            navigation = self._navigation_for(results)
            match_type = _exact_match_type(identifier, results)
            return CatalogChatOutcome(
                identifier=identifier,
                status="found",
                answer=format_catalog_answer(results, navigation=navigation),
                results=results,
                route="catalog_exact_lookup",
                match_type=match_type,
                catalog_sources=_catalog_sources_for(results),
                navigation=navigation,
            )

        LOGGER.info("Catalog exact lookup not found: identifier=%s", identifier)
        if not is_part_number_like(identifier):
            return CatalogChatOutcome(
                identifier=identifier,
                status="not_found",
                answer=(
                    f"Точного совпадения по коду {identifier} "
                    "в каталоге не найдено."
                ),
                match_type="catalog_exact",
            )
        candidate_search = getattr(search, "search_part_number_candidates", None)
        code_candidates = CatalogCodeSearchResult(match_type=None)
        if callable(candidate_search):
            try:
                code_candidates = candidate_search(
                    identifier,
                    limit=DEFAULT_CATALOG_CANDIDATE_LIMIT,
                )
            except Exception as exc:
                LOGGER.warning("Catalog code search failed: %s", type(exc).__name__)
                return _unavailable(
                    identifier=identifier,
                    search_query=identifier,
                    route="catalog_code_search",
                    error=type(exc).__name__,
                )
        if code_candidates.results:
            results = tuple(code_candidates.results)[:DEFAULT_CATALOG_CANDIDATE_LIMIT]
            return CatalogChatOutcome(
                identifier=identifier,
                search_query=identifier,
                route="catalog_code_search",
                status="candidates",
                match_type=code_candidates.match_type,
                answer=format_catalog_code_candidates(
                    identifier,
                    results,
                    match_type=code_candidates.match_type or "catalog_prefix",
                ),
                results=results,
                catalog_sources=_catalog_sources_for(results),
            )

        fts_search = getattr(search, "search_parts", None)
        if callable(fts_search) and (explicit_text_search or callable(candidate_search)):
            try:
                fts_results = tuple(
                    fts_search(identifier, limit=DEFAULT_CATALOG_CANDIDATE_LIMIT)
                )[:DEFAULT_CATALOG_CANDIDATE_LIMIT]
            except Exception as exc:
                LOGGER.warning("Catalog FTS fallback failed: %s", type(exc).__name__)
                return _unavailable(
                    identifier=identifier,
                    search_query=identifier,
                    route="catalog_code_search",
                    error=type(exc).__name__,
                )
            if fts_results:
                return CatalogChatOutcome(
                    identifier=identifier,
                    search_query=identifier,
                    route="catalog_code_search",
                    status="candidates",
                    match_type="catalog_fts",
                    answer=format_catalog_code_candidates(
                        identifier,
                        fts_results,
                        match_type="catalog_fts",
                    ),
                    results=fts_results,
                    catalog_sources=_catalog_sources_for(fts_results),
                )

        if not explicit_text_search and not callable(candidate_search):
            return CatalogChatOutcome(
                identifier=identifier,
                status="not_found",
                answer=(
                    f"Точного совпадения по коду {identifier} "
                    "в каталоге не найдено."
                ),
                match_type="catalog_exact",
            )
        return CatalogChatOutcome(
            identifier=identifier,
            search_query=identifier if explicit_text_search else None,
            route="catalog_code_search" if explicit_text_search else "catalog_exact_lookup",
            status="no_candidates",
            answer=(
                f"Точных или частичных совпадений по коду {identifier} "
                "в каталоге не найдено."
            ),
        )

    def _navigation_for(
        self,
        results: tuple[CatalogPartResult, ...],
    ) -> tuple[CatalogOccurrenceNavigation, ...]:
        if self.page_locator is None:
            return ()
        navigation = []
        for result in results:
            try:
                navigation.append(self.page_locator.locate_occurrence(result))
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "Catalog navigation unavailable for occurrence: %s",
                    type(exc).__name__,
                )
        return tuple(navigation)

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
                match_type="catalog_fts",
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
            match_type="catalog_fts",
            answer=format_catalog_candidates(query, results),
            results=results,
            catalog_sources=_catalog_sources_for(results),
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
    embedded = _extract_embedded_part_number(clean)
    if embedded is not None:
        return embedded
    candidate = _clean_candidate(clean)
    if not _BARE_CODE.fullmatch(candidate):
        return None
    compact = re.sub(r"[ ._/-]", "", candidate)
    if compact.isdecimal():
        # Six normalized characters is the guarded substring-search threshold.
        return candidate if len(compact) >= MIN_CODE_SUBSTRING_LENGTH else None
    return candidate if any(char.isdigit() for char in compact) and any(char.isalpha() for char in compact) else None


def _extract_embedded_part_number(question: str) -> str | None:
    """Extract a technical code only when natural wording names its code role."""
    patterns = (
        *_NATURAL_CODE_PATTERNS,
        re.compile(
            r"\b(?P<code>(?:икс|[A-ZА-ЯЁ])\s*[- ._/]?\s*\d[A-ZА-ЯЁ0-9 ._/-]{3,29})"
            r"\s*[—-]?\s*(?:начал\w*\s+кода)",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(question)
        if match is None:
            continue
        candidate = _clean_candidate(match.group("code"))
        candidate = re.sub(r"^икс\s*[- ._/]?\s*", "X", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+", "", candidate)
        if is_part_number_like(candidate):
            return candidate
    return None


def extract_catalog_text_search(question: str) -> str | None:
    """Extract only the payload of an explicit catalog text-search command."""
    for pattern in _TEXT_SEARCH_PATTERNS:
        match = pattern.fullmatch(question)
        if match:
            return _clean_text_search_query(match.group("query"))
    return None


def extract_structured_catalog_intent(
    question: str,
) -> StructuredCatalogIntent | None:
    """Parse explicit assembly/part/position wording before any RAG routing."""
    for pattern in _BOM_POSITION_PATTERNS:
        match = pattern.fullmatch(question)
        if match:
            return StructuredCatalogIntent(
                kind="bom_position",
                code=_clean_candidate(match.group("code")),
                position=_clean_candidate(match.group("position")),
            )
    for pattern in _ASSEMBLY_CONTENT_PATTERNS:
        match = pattern.fullmatch(question)
        if match:
            return StructuredCatalogIntent(
                kind="assembly_contents",
                code=_clean_candidate(match.group("code")),
            )
    for pattern in _ASSEMBLY_EXACT_PATTERNS:
        match = pattern.fullmatch(question)
        if match:
            return StructuredCatalogIntent(
                kind="assembly_exact",
                code=_clean_candidate(match.group("code")),
            )
    for pattern in _PART_EXACT_PATTERNS:
        match = pattern.fullmatch(question)
        if match:
            return StructuredCatalogIntent(
                kind="part_exact",
                code=_clean_candidate(match.group("code")),
            )
    return None


def format_assembly_contents(
    assemblies: tuple[CatalogAssemblyResult, ...],
    results: tuple[CatalogPartResult, ...],
) -> str:
    first = assemblies[0]
    label = first.assembly_code
    if first.assembly_name:
        label += f" — {first.assembly_name}"
    lines = [f"Узел: {label}", f"Позиций спецификации: {len(results)}"]
    if first.source_filename:
        lines.append(f"Документ: {first.source_filename}")
    if first.revision:
        lines.append(f"Ревизия: {first.revision}")
    if first.machine_number:
        lines.append(f"Машина: {first.machine_number}")
    for result in results:
        part = result.part_number
        if result.part_name:
            part += f" — {result.part_name}"
        lines.extend(("", f"Позиция {result.position}: {part}"))
        details = []
        if result.quantity is not None:
            details.append(
                "Количество по спецификации: "
                f"{_format_quantity_with_unit(result.quantity, result.unit)}"
            )
        details.append(f"Страница спецификации: {result.source_page}")
        if result.reference_page is not None:
            details.append(f"Связанная страница: {result.reference_page}")
        lines.append(" · ".join(details))
    return "\n".join(lines)


def format_bom_position(
    assemblies: tuple[CatalogAssemblyResult, ...],
    results: tuple[CatalogPartResult, ...],
    *,
    position: str,
) -> str:
    assembly = assemblies[0]
    label = assembly.assembly_code
    if assembly.assembly_name:
        label += f" — {assembly.assembly_name}"
    lines = [f"Узел: {label}", f"Позиция: {position}"]
    for result in results:
        part = result.part_number
        if result.part_name:
            part += f" — {result.part_name}"
        lines.append(f"Деталь: {part}")
        if result.quantity is not None:
            lines.append(
                "Количество по спецификации: "
                f"{_format_quantity_with_unit(result.quantity, result.unit)}"
            )
        lines.append(f"Страница спецификации: {result.source_page}")
        if result.reference_page is not None:
            lines.append(f"Связанная страница: {result.reference_page}")
    return "\n".join(lines)


def format_catalog_entity_ambiguity(
    identifier: str,
    assemblies: tuple[CatalogAssemblyResult, ...],
    part_results: tuple[CatalogPartResult, ...],
) -> str:
    lines = [f"По коду {identifier} найдено несколько значений:"]
    for assembly in assemblies:
        label = f"узел {assembly.assembly_code}"
        if assembly.assembly_name:
            label += f" — {assembly.assembly_name}"
        lines.append(f"- {label}; позиций спецификации: {assembly.bom_item_count}.")
    for result in part_results:
        label = f"деталь/подузел {result.part_number}"
        if result.part_name:
            label += f" — {result.part_name}"
        lines.append(
            f"- {label}; используется в узле {result.assembly_code}, "
            f"позиция {result.position}, страница спецификации {result.source_page}."
        )
    lines.append(
        "Уточните «узел " + identifier + "» или «деталь " + identifier + "»."
    )
    return "\n".join(lines)


def format_catalog_answer(
    results: tuple[CatalogPartResult, ...],
    *,
    navigation: tuple[CatalogOccurrenceNavigation, ...] = (),
) -> str:
    first = results[0]
    lines = [f"Деталь: {first.part_number}"]
    if first.part_name:
        lines.append(f"Наименование: {first.part_name}")
    lines.append(f"Найдено вхождений: {len(results)}")
    for index, result in enumerate(results[:MAX_RENDERED_OCCURRENCES], 1):
        occurrence_navigation = (
            navigation[index - 1] if index <= len(navigation) else None
        )
        assembly = result.assembly_code
        if result.assembly_name:
            assembly += f" — {result.assembly_name}"
        lines.extend(
            (
                "",
                f"{index}. Узел: {assembly}",
                f"   {_format_specification_details(result)}",
            )
        )
        if result.reference_page is not None:
            lines.append(f"   Связанная страница: {result.reference_page}")
        if occurrence_navigation is not None:
            lines.append(
                f"   Документ: {occurrence_navigation.document.source_filename}"
            )
            if occurrence_navigation.source.status == "unavailable":
                lines.append("   Навигация: исходный PDF каталога недоступен локально.")
            elif occurrence_navigation.source.status == "invalid_page":
                lines.append(
                    "   Навигация: страница спецификации вне диапазона документа."
                )
            if (
                occurrence_navigation.reference is not None
                and occurrence_navigation.reference.status == "invalid_page"
            ):
                lines.append("   Навигация: связанная страница вне диапазона документа.")
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


def format_catalog_code_candidates(
    query: str,
    results: tuple[CatalogPartResult, ...],
    *,
    match_type: str,
) -> str:
    if match_type in {"catalog_prefix", "catalog_normalized_prefix"}:
        lines = [
            f"По частичному коду «{query}» найдено деталей: {len(results)}."
        ]
        explanation = "Это детерминированные prefix-совпадения по коду детали."
    elif match_type == "catalog_substring":
        lines = [
            f"По фрагменту кода «{query}» найдено деталей: {len(results)}."
        ]
        explanation = "Это детерминированные partial code matches."
    else:
        lines = [
            f"Точных или частичных совпадений по коду «{query}» не найдено.",
            f"Показано кандидатов полнотекстового поиска: {len(results)}.",
        ]
        explanation = (
            "Это кандидаты полнотекстового поиска, а не совпадения по коду детали."
        )
    for index, result in enumerate(results, 1):
        label = result.part_number
        if result.part_name:
            label += f" — {result.part_name}"
        lines.append(f"{index}. {label}")
    lines.extend(
        (
            "",
            explanation,
            "Чтобы получить все вхождения и данные спецификации, "
            "отправьте: «Найди <код детали>».",
        )
    )
    return "\n".join(lines)


def format_natural_catalog_candidates(
    query: str,
    results: tuple[CatalogPartResult, ...],
) -> str:
    lines = [
        f"В каталоге запасных частей по запросу «{query}» найдены детали и узлы.",
        "",
    ]
    for index, result in enumerate(results, 1):
        label = f"{index}. {result.part_number}"
        if result.part_name:
            label += f" — {result.part_name}"
        lines.append(label)
        lines.append(
            f"   Узел: {result.assembly_code}"
            + (f" — {result.assembly_name}" if result.assembly_name else "")
        )
        lines.append(f"   {_format_specification_details(result)}")
    lines.extend(
        (
            "",
            "Это структурированные позиции спецификации; назначение узла "
            "каталог не описывает.",
            "Укажите код детали, чтобы получить все её вхождения.",
        )
    )
    return "\n".join(lines)


def _clean_candidate(value: str) -> str:
    return value.strip().strip("?!.:,;\"'«»").strip()


def _structured_route(kind: str) -> str:
    return {
        "assembly_exact": "catalog_assembly_exact",
        "assembly_contents": "catalog_assembly_contents",
        "bom_position": "catalog_bom_position",
        "part_exact": "catalog_part_exact",
    }[kind]


def _is_bare_code_question(question: str, identifier: str) -> bool:
    return _clean_candidate(question) == identifier


def _format_quantity_with_unit(quantity: object, unit: str | None) -> str:
    rendered = format_catalog_quantity(quantity)
    if not unit:
        return rendered
    clean_unit = unit.strip().rstrip(".")
    suffix = f"{clean_unit}." if clean_unit else ""
    return " ".join(part for part in (rendered, suffix) if part)


def _format_specification_details(result: CatalogPartResult) -> str:
    details = [f"Позиция: {result.position}"]
    if result.quantity is not None:
        details.append(
            "Количество по спецификации: "
            f"{_format_quantity_with_unit(result.quantity, result.unit)}"
        )
    details.append(f"Страница спецификации: {result.source_page}")
    return " · ".join(details)


def _clean_text_search_query(value: str) -> str:
    return value.strip().strip("?!.:,;\"'«»").strip()


def _is_explicit_candidate(candidate: str) -> bool:
    if _EXPLICIT_CODE.fullmatch(candidate) and any(char.isdigit() for char in candidate):
        return True
    return bool(_UPPERCASE_UNKNOWN.fullmatch(candidate) and "_" in candidate)


def _exact_match_type(
    query: str,
    results: tuple[CatalogPartResult, ...],
) -> str:
    original = query.strip()
    if any(result.part_number == original for result in results):
        return "catalog_exact"
    return "catalog_normalized_exact"


def _catalog_sources_for(
    results: tuple[CatalogPartResult, ...],
) -> tuple[CatalogSource, ...]:
    return tuple(
        CatalogSource(
            part_number=result.part_number,
            assembly_code=result.assembly_code,
            position=result.position,
            source_page=result.source_page,
            reference_page=result.reference_page,
            source_filename=result.source_filename,
            revision=result.revision,
            machine_number=result.machine_number,
            catalog_id=result.catalog_id,
        )
        for result in results
    )


def _natural_catalog_probe_query(question: str) -> tuple[str | None, bool]:
    """Compatibility wrapper around structured natural-query extraction."""
    extracted = _extract_natural_catalog_query(question)
    if extracted is None:
        terms = _catalog_terms(question)
        return None, (
            _contains_procedural_signal(terms)
            or ("делать" in terms and "надо" in terms)
        ) and _has_catalog_language_signal(
            question
        )
    return extracted.query, extracted.mixed_signal


def _extract_natural_catalog_query(question: str) -> NaturalCatalogQuery | None:
    tokens = _catalog_terms(question)
    if not tokens:
        return None
    mixed_signal = _contains_procedural_signal(tokens) or (
        "делать" in tokens and "надо" in tokens
    )
    corporate_signal = any(token in _CORPORATE_ROUTE_TERMS for token in tokens)
    catalog_language_signal = _has_catalog_language_signal(question)
    if corporate_signal and (not mixed_signal or not catalog_language_signal):
        return None

    catalog_clause, procedure_clause = _catalog_clauses(
        question,
        mixed_signal=mixed_signal,
    )
    for pattern in _NATURAL_ENTITY_CONTEXT_PATTERNS:
        match = pattern.fullmatch(catalog_clause)
        if match:
            entity = _clean_natural_phrase(match.group("entity"))
            context = _clean_natural_phrase(match.group("context"))
            entity_terms = _natural_content_terms(entity)
            if entity_terms and context:
                entity = " ".join(entity_terms)
                context, normalization = _normalize_assembly_context(context)
                query = f"{entity} {context}"
                return NaturalCatalogQuery(
                    query=query,
                    subject=query,
                    entity_terms=entity_terms,
                    assembly_context=context,
                    mixed_signal=mixed_signal,
                    catalog_clause=catalog_clause,
                    procedure_clause=procedure_clause,
                    resolved_referent=(query if procedure_clause else None),
                    understanding_pattern="entity_with_assembly_context",
                    normalization_applied=normalization,
                )

    subject = None
    for pattern in _NATURAL_SUBJECT_PATTERNS:
        match = pattern.fullmatch(catalog_clause)
        if match:
            subject = _clean_natural_phrase(match.group("subject"))
            break
    raw_subject = subject or catalog_clause
    normalized_subject, morphology_normalization = _normalize_catalog_phrase(
        raw_subject
    )
    content = list(_natural_content_terms(raw_subject))
    if not MIN_NATURAL_PROBE_TERMS <= len(content) <= MAX_NATURAL_PROBE_TERMS:
        return None
    query = " ".join(content)
    resolved_subject = " ".join(_natural_content_terms(normalized_subject)) or query
    return NaturalCatalogQuery(
        query=query,
        subject=resolved_subject,
        mixed_signal=mixed_signal,
        catalog_clause=catalog_clause,
        procedure_clause=procedure_clause,
        resolved_referent=(query if procedure_clause else None),
        understanding_pattern="catalog_subject",
        normalization_applied=tuple(
            dict.fromkeys(
                [
                    *morphology_normalization,
                    *(("boilerplate_removed",) if query != catalog_clause.casefold() else ()),
                ]
            )
        ),
    )


def _catalog_clause(question: str, *, mixed_signal: bool) -> str:
    return _catalog_clauses(question, mixed_signal=mixed_signal)[0]


def _catalog_clauses(
    question: str,
    *,
    mixed_signal: bool,
) -> tuple[str, str | None]:
    """Split one catalog clause from its procedural/corporate continuation."""
    clean = question.strip()
    if not mixed_signal:
        return clean, None
    boundaries = tuple(
        re.finditer(
            r"\s*(?:;|,)\s*(?:потом|затем|а\s+затем)?\s*|\s+и\s+",
            clean,
            flags=re.IGNORECASE,
        )
    )
    for boundary in boundaries:
        head = clean[: boundary.start()].strip()
        tail = clean[boundary.end() :].strip()
        tail_tokens = _catalog_terms(tail)
        if not head or not tail:
            continue
        if _contains_procedural_signal(tail_tokens) or (
            "делать" in tail_tokens and "надо" in tail_tokens
        ):
            return head, tail
    return clean, None


def _has_catalog_language_signal(question: str) -> bool:
    normalized = question.casefold().replace("ё", "е")
    phrases = (
        "какие детали",
        "найди детали",
        "что входит",
        "что стоит в",
        "что находится в",
        "что установлено в",
        "что есть в",
        "перечисли комплектующие",
        "перечисли детали",
        "подбери вал",
        "вал в ",
        "вал для ",
        "началом кода",
        "в коде",
        "каталоге",
        "узел",
        "части",
        "устройство",
    )
    return any(phrase in normalized for phrase in phrases)


def _clean_natural_phrase(value: str) -> str:
    return " ".join(value.strip(" \t\r\n?!.:").split())


_CATALOG_WORD_FORMS = {
    "прижимному": "прижимное",
    "прижимного": "прижимное",
    "прижимном": "прижимное",
    "устройства": "устройство",
    "устройстве": "устройство",
    "устройству": "устройство",
    "узла": "узел",
    "узле": "узел",
}


def _normalize_catalog_phrase(value: str) -> tuple[str, tuple[str, ...]]:
    clean = _clean_natural_phrase(value)
    tokens = clean.split()
    normalized = [_CATALOG_WORD_FORMS.get(token.casefold(), token) for token in tokens]
    rendered = " ".join(normalized)
    return rendered, (("domain_word_forms_normalized",) if rendered != clean else ())


def _natural_content_terms(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _catalog_terms(value)
        if token not in _NATURAL_CATALOG_STOP_WORDS
        and token not in _MIXED_ROUTE_TERMS
        and token not in _PROCEDURAL_QUERY_TOKENS
        and not _is_procedural_term(token)
        and len(token) >= MIN_NATURAL_PROBE_TERM_LENGTH
    )


def _normalize_assembly_context(value: str) -> tuple[str, tuple[str, ...]]:
    clean, morphology = _normalize_catalog_phrase(value)
    lower_match = re.fullmatch(r"(?:внизу|снизу)\s+(.+)", clean, re.IGNORECASE)
    if lower_match:
        return f"нижняя часть {lower_match.group(1)}", tuple(
            dict.fromkeys((*morphology, "relative_position_normalized"))
        )
    upper_match = re.fullmatch(r"(?:наверху|сверху)\s+(.+)", clean, re.IGNORECASE)
    if upper_match:
        return f"верхняя часть {upper_match.group(1)}", tuple(
            dict.fromkeys((*morphology, "relative_position_normalized"))
        )
    return clean, morphology


def _contains_procedural_signal(tokens: tuple[str, ...]) -> bool:
    return any(
        token in _MIXED_ROUTE_TERMS
        or token in _PROCEDURAL_QUERY_TOKENS
        or _is_procedural_term(token)
        for token in tokens
    )


def _is_procedural_term(token: str) -> bool:
    normalized = token.casefold().replace("ё", "е")
    return any(normalized.startswith(stem) for stem in _PROCEDURAL_TERM_STEMS)


def _catalog_terms(query: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", query.casefold()))


def _result_field_coverage(
    result: CatalogPartResult,
    terms: tuple[str, ...],
) -> float:
    searchable_tokens = tuple(
        _term_stem(token)
        for token in re.findall(
            r"[0-9A-Za-zА-Яа-яЁё]+",
            " ".join(
                value.casefold()
                for value in (
                    result.part_name or "",
                    result.assembly_code or "",
                    result.assembly_name or "",
                    result.equipment_model or "",
                )
            ),
        )
    )
    if not terms:
        return 0.0
    return sum(
        _term_stem(term) in searchable_tokens for term in terms
    ) / len(terms)


def _natural_probe_candidates(
    search: CatalogSearchClient,
    query: str,
    *,
    entity_terms: tuple[str, ...] = (),
    assembly_context: str | None = None,
) -> tuple[CatalogPartResult, ...]:
    direct = tuple(
        search.search_parts(
            assembly_context if assembly_context else query,
            limit=(20 if assembly_context else DEFAULT_CATALOG_CANDIDATE_LIMIT),
        )
    )
    if direct and assembly_context:
        context_terms = _catalog_terms(assembly_context)
        coherent = tuple(
            result
            for result in direct
            if _field_coverage(result, entity_terms, fields="part") == 1.0
            and _field_coverage(result, context_terms, fields="assembly") == 1.0
        )
        if coherent:
            return coherent[:DEFAULT_CATALOG_CANDIDATE_LIMIT]
    if direct:
        if not assembly_context:
            return direct[:DEFAULT_CATALOG_CANDIDATE_LIMIT]

    terms = _catalog_terms(query)
    candidates: dict[str, tuple[CatalogPartResult, int, int]] = {}
    search_terms = list(terms)
    if assembly_context:
        search_terms.extend(_catalog_terms(assembly_context))
    for term in dict.fromkeys(search_terms):
        search_term = _term_stem(term)
        for rank, result in enumerate(search.search_parts(search_term, limit=20), 1):
            existing = candidates.get(result.part_number)
            if existing is None:
                candidates[result.part_number] = (result, 1, rank)
            else:
                candidates[result.part_number] = (
                    existing[0],
                    existing[1] + 1,
                    existing[2] + rank,
                )
    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            -_result_field_coverage(item[0], terms),
            -item[1],
            item[2],
            item[0].part_number,
        ),
    )
    results = tuple(item[0] for item in ranked)
    if assembly_context:
        context_terms = _catalog_terms(assembly_context)
        results = tuple(
            result
            for result in results
            if _field_coverage(result, entity_terms, fields="part") == 1.0
            and _field_coverage(result, context_terms, fields="assembly") == 1.0
        )
    return results[:DEFAULT_CATALOG_CANDIDATE_LIMIT]


def _field_coverage(
    result: CatalogPartResult,
    terms: tuple[str, ...],
    *,
    fields: str,
) -> float:
    if not terms:
        return 0.0
    if fields == "part":
        values = (result.part_number, result.part_name or "")
    else:
        values = (result.assembly_code or "", result.assembly_name or "")
    tokens = {
        _term_stem(token)
        for token in _catalog_terms(" ".join(values))
    }
    return sum(_term_stem(term) in tokens for term in terms) / len(terms)


_RUSSIAN_TERM_SUFFIXES = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ая", "яя", "ое", "ее", "ой", "ей", "ий", "ый", "ом", "ем",
    "ах", "ях", "ую", "юю", "а", "я", "ы", "и", "у", "ю", "е", "о", "ь",
)


def _term_stem(term: str) -> str:
    value = term.casefold().replace("ё", "е")
    if not re.fullmatch(r"[а-я]+", value) or len(value) < 5:
        return value
    for suffix in _RUSSIAN_TERM_SUFFIXES:
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            return value[: -len(suffix)]
    return value


def _source_requirements(
    subject: str,
    *,
    mixed_signal: bool,
    catalog_status: str,
) -> tuple[SourceRequirement, ...]:
    requirements = [
        SourceRequirement(
            requirement_id="catalog_entity_information",
            subject=subject,
            source="catalog",
            status=catalog_status,
        )
    ]
    if mixed_signal:
        requirements.append(
            SourceRequirement(
                requirement_id="maintenance_procedure",
                subject=subject,
                source="corporate",
            )
        )
    return tuple(requirements)


def _empty_probe(
    query: str,
    *,
    performed: bool,
    mixed_signal: bool = False,
    natural_query: NaturalCatalogQuery | None = None,
) -> CatalogProbeDiagnostics:
    return CatalogProbeDiagnostics(
        performed=performed,
        query=query,
        result_count=0,
        top_score=None,
        top_field_coverage=0.0,
        coherent_result_count=0,
        source_route="mixed" if mixed_signal else "corporate",
        outcome=None,
        resolved_requirements=(
            _source_requirements(
                natural_query.subject if natural_query is not None else query,
                mixed_signal=mixed_signal,
                catalog_status="not_found",
            )
            if performed or mixed_signal
            else ()
        ),
        catalog_subject=(natural_query.subject if natural_query is not None else None),
        catalog_entity_terms=(
            natural_query.entity_terms if natural_query is not None else ()
        ),
        catalog_assembly_context=(
            natural_query.assembly_context if natural_query is not None else None
        ),
        catalog_clause=(
            natural_query.catalog_clause if natural_query is not None else None
        ),
        procedure_clause=(
            natural_query.procedure_clause if natural_query is not None else None
        ),
        resolved_referent=(
            natural_query.resolved_referent if natural_query is not None else None
        ),
        understanding_pattern=(
            natural_query.understanding_pattern if natural_query is not None else None
        ),
        normalization_applied=(
            natural_query.normalization_applied if natural_query is not None else ()
        ),
    )


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
