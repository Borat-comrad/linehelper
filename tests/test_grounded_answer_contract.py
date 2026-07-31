from __future__ import annotations

import json

from linehelper.rag.answer_contract import (
    MISSING_INFORMATION_HEADING,
    AnswerContractBuilder,
    AnswerContractValidator,
    GroundedAnswerRenderer,
)
from linehelper.rag.evidence_assessor import (
    EvidenceDecision,
    EvidencePlan,
    EvidenceRequirement,
)
from linehelper.rag.retriever import RetrievedChunk


def test_full_answer_contract_contains_only_supported_evidence() -> None:
    chunk = _chunk(29, "Заявка в хозчасть")
    contract = AnswerContractBuilder().build(
        _decision(
            "full_answer",
            supported=[("primary_procedure", "основная процедура", (29,))],
            supporting=[chunk],
        )
    )

    assert contract.answer_mode == "full_answer"
    assert contract.allowed_chunk_ids == (29,)
    assert contract.supported_requirement_ids == ("primary_procedure",)
    assert contract.unsupported_requirement_ids == ()
    assert contract.required_sections == ("supported_answer", "sources")


def test_partial_answer_contract_separates_unsupported_requirements() -> None:
    contract = AnswerContractBuilder().build(
        _decision(
            "partial_answer",
            supported=[
                (
                    "related_submission_procedure",
                    "процедура подачи",
                    (47,),
                )
            ],
            unsupported=[
                (
                    "named_document_recipient",
                    "именованный адресат",
                )
            ],
            supporting=[_chunk(47, "Командировка")],
        )
    )

    prompt_contract = contract.to_prompt_dict()
    assert contract.unsupported_requirement_ids == (
        "named_document_recipient",
    )
    assert "unsupported_requirements" not in prompt_contract
    assert "именованный адресат" not in json.dumps(
        prompt_contract,
        ensure_ascii=False,
    )


def test_insufficient_evidence_contract_renders_without_draft() -> None:
    contract = AnswerContractBuilder().build(
        _decision(
            "insufficient_evidence",
            unsupported=[("primary_fact", "основной факт")],
        )
    )
    validation = AnswerContractValidator().validate("", contract)
    rendered = GroundedAnswerRenderer().render("", contract, validation)

    assert validation.valid is True
    assert rendered.source_entries == ()
    assert MISSING_INFORMATION_HEADING in rendered.answer
    assert "- основной факт" in rendered.answer
    assert rendered.final_answer_sections == ("missing_information",)


def test_partial_renderer_adds_deterministic_missing_information() -> None:
    contract = AnswerContractBuilder().build(
        _decision(
            "partial_answer",
            supported=[("known", "известная часть", (1,))],
            unsupported=[
                ("missing_a", "первый отсутствующий пункт"),
                ("missing_b", "второй отсутствующий пункт"),
            ],
            supporting=[_chunk(1, "Документ")],
        )
    )
    validation = AnswerContractValidator().validate(
        "Подтверждённая часть.",
        contract,
    )
    rendered = GroundedAnswerRenderer().render(
        "Подтверждённая часть.",
        contract,
        validation,
    )

    assert rendered.answer == (
        "Подтверждённая часть.\n\n"
        f"{MISSING_INFORMATION_HEADING}\n"
        "- первый отсутствующий пункт\n"
        "- второй отсутствующий пункт"
    )


def test_source_entries_are_deduplicated_in_supporting_order() -> None:
    first = _chunk(7, "Первый")
    second = _chunk(8, "Второй")
    contract = AnswerContractBuilder().build(
        _decision(
            "full_answer",
            supported=[
                ("requirement_a", "пункт A", (7, 8)),
                ("requirement_b", "пункт B", (7,)),
            ],
            supporting=[first, first, second],
        )
    )

    assert [source.chunk_id for source in contract.source_entries] == [7, 8]
    assert contract.source_entries[0].supported_requirement_ids == (
        "requirement_a",
        "requirement_b",
    )
    assert contract.source_entries[1].supported_requirement_ids == (
        "requirement_a",
    )


def test_non_supporting_chunks_are_excluded_from_allowed_sources() -> None:
    contract = AnswerContractBuilder().build(
        _decision(
            "full_answer",
            supported=[("primary", "основной факт", (29,))],
            supporting=[_chunk(29, "Основной документ")],
            non_supporting=[
                _chunk(44, "Шум 1"),
                _chunk(39, "Шум 2"),
            ],
        )
    )

    assert contract.allowed_chunk_ids == (29,)
    assert contract.diagnostics_metadata["non_supporting_chunk_ids"] == [
        44,
        39,
    ]


def test_contract_validation_uses_safe_fallback_for_disallowed_source() -> None:
    contract = AnswerContractBuilder().build(
        _decision(
            "full_answer",
            supported=[("primary", "основной факт", (29,))],
            supporting=[_chunk(29, "Разрешённый документ")],
        )
    )
    draft = "Ответ.\nИсточник: Запрещённый документ\nchunk 44"
    validation = AnswerContractValidator().validate(draft, contract)
    rendered = GroundedAnswerRenderer().render(
        draft,
        contract,
        validation,
    )

    assert validation.valid is False
    assert validation.fallback_applied is True
    assert "disallowed_source_reference" in validation.violations
    assert "Запрещённый документ" not in rendered.answer
    assert "chunk 44" not in rendered.answer


def test_contract_diagnostics_are_json_serializable() -> None:
    contract = AnswerContractBuilder().build(
        _decision(
            "partial_answer",
            supported=[("known", "известная часть", (47,))],
            unsupported=[("missing", "неизвестная часть")],
            supporting=[_chunk(47, "Командировка")],
        )
    )
    validation = AnswerContractValidator().validate("Ответ.", contract)
    rendered = GroundedAnswerRenderer().render(
        "Ответ.",
        contract,
        validation,
    )

    payload = {
        "answer_contract": contract.to_dict(),
        "contract_validation": validation.to_dict(),
        "render": rendered.to_dict(),
    }
    serialized = json.loads(json.dumps(payload, ensure_ascii=False))
    assert serialized["answer_contract"]["allowed_chunk_ids"] == [47]
    assert serialized["contract_validation"]["valid"] is True
    assert serialized["render"]["final_answer_sections"] == [
        "supported_answer",
        "missing_information",
        "sources",
    ]


def _decision(
    mode: str,
    *,
    supported: list[tuple[str, str, tuple[int | str, ...]]] | None = None,
    unsupported: list[tuple[str, str]] | None = None,
    supporting: list[RetrievedChunk] | None = None,
    non_supporting: list[RetrievedChunk] | None = None,
) -> EvidenceDecision:
    supported_requirements = tuple(
        EvidenceRequirement(
            requirement_id=requirement_id,
            requirement_type="test",
            description=description,
            supported=True,
            supporting_chunk_ids=chunk_ids,
            assessment_reasons=("test_support",),
        )
        for requirement_id, description, chunk_ids in supported or []
    )
    unsupported_requirements = tuple(
        EvidenceRequirement(
            requirement_id=requirement_id,
            requirement_type="test",
            description=description,
            supported=False,
            rejection_reasons=("test_missing",),
        )
        for requirement_id, description in unsupported or []
    )
    assessed = (*supported_requirements, *unsupported_requirements)
    plan = EvidencePlan(
        requested_fact_type="unknown",
        answer_shape="default",
        evidence_requirements=assessed,
        allow_partial_answer=True,
        minimum_supported_requirements=1,
    )
    return EvidenceDecision(
        mode=mode,
        plan=plan,
        assessed_requirements=assessed,
        supported_requirements=tuple(
            requirement.requirement_id
            for requirement in supported_requirements
        ),
        unsupported_requirements=tuple(
            requirement.requirement_id
            for requirement in unsupported_requirements
        ),
        supporting_chunks=tuple(supporting or []),
        non_supporting_chunks=tuple(non_supporting or []),
        decision_reasons=("test_decision",),
    )


def _chunk(chunk_id: int, title: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        title=title,
        source=f"data/raw_docs/{title}.pdf",
        section=f"Раздел {chunk_id}",
        page=1,
        text=f"Текст {title}.",
        score=100.0,
        metadata={
            "record_key": f"record:{chunk_id}",
            "logical_unit_title": f"Раздел {chunk_id}",
        },
        final_score=100.0,
    )
