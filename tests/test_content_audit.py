from pathlib import Path

from PIL import Image

from presentation_video.application.cinematic import compile_shots
from presentation_video.application.content_audit import (
    attach_critical_information,
    audit_critical_information,
    validate_critical_information_coverage,
    validate_visual_critical_information_coverage,
)
from presentation_video.application.information_cards import render_information_card
from presentation_video.application.whiteboard import compile_whiteboard_shots
from presentation_video.domain.models import (
    BrandKit,
    CriticalInformationKind,
    MediaMode,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    ProductionMode,
    SceneScript,
    SlideContent,
    VisualScenePlan,
)


_APPROVAL_PAGE = """
7. Limites e alçadas
Valor total da solicitação
Aprovação mínima
Até R$ 500,00
Gestor imediato
De R$ 500,01 a R$ 3.000,00
Gerente da área
De R$ 3.000,01 a R$ 10.000,00
Diretor da área
Acima de R$ 10.000,00
Diretor da área e Financeiro
O fracionamento de despesas para reduzir a alçada de aprovação é proibido.
9. Prazo e fluxo
Registrar
Até 10 dias corridos após a despesa.
Aprovar
Gestor analisa em até 5 dias úteis.
Pagar
Até 10 dias úteis após aprovação final.
"""


def _document(tmp_path: Path) -> PresentationDocument:
    source = tmp_path / "source.pdf"
    source.touch()
    frame = tmp_path / "slide-004.png"
    Image.new("RGB", (1280, 720), "white").save(frame)
    return PresentationDocument(
        source_path=source,
        title="Normativo",
        slides=[
            SlideContent(
                number=4,
                title="Limites e alçadas",
                body_text=_APPROVAL_PAGE,
                image_path=frame,
            )
        ],
    )


def _script(units) -> PresentationScript:
    base = PresentationScript(
        title="Reembolso",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[4],
                narration=(
                    "Registre a solicitação no prazo indicado. As alçadas de aprovação mudam "
                    "conforme o valor total. Depois, o Financeiro realiza o pagamento."
                ),
                target_seconds=32,
                media_mode=MediaMode.VIDEO,
                story_beat="Fluxo de reembolso",
                visual_intent="Colaborador registra um recibo.",
            )
        ],
        total_estimated_seconds=32,
    )
    return attach_critical_information(base, units)


def _plan(script: PresentationScript, shots) -> PresentationVisualPlan:
    return PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[4],
                media_mode=MediaMode.VIDEO,
                prompt="Employee submits a reimbursement request.",
                shots=shots,
                critical_information=script.scenes[0].critical_information,
            )
        ]
    )


def test_audit_extracts_approval_matrix_and_deadlines(tmp_path: Path) -> None:
    units = audit_critical_information(_document(tmp_path))

    approval = next(unit for unit in units if unit.kind == CriticalInformationKind.APPROVAL_MATRIX)
    deadlines = next(unit for unit in units if unit.kind == CriticalInformationKind.DEADLINE)

    assert approval.mandatory is True
    assert approval.exact_display_required is True
    assert approval.facts[:2] == [
        "Até R$ 500,00 — Gestor imediato",
        "De R$ 500,01 a R$ 3.000,00 — Gerente da área",
    ]
    assert any("10 dias corridos" in fact for fact in deadlines.facts)


def test_corporate_timeline_inserts_deterministic_static_information_takes(
    tmp_path: Path,
) -> None:
    units = audit_critical_information(_document(tmp_path))
    script = _script(units)
    shots = compile_shots(
        VisualScenePlan(
            scene_number=1,
            source_slide_numbers=[4],
            media_mode=MediaMode.VIDEO,
            prompt="Employee submits a reimbursement request.",
        ),
        script.scenes[0],
        32,
    )

    static_shots = [shot for shot in shots if shot.locked_static]
    assert {unit.kind for shot in static_shots for unit in shot.critical_information} >= {
        CriticalInformationKind.APPROVAL_MATRIX,
        CriticalInformationKind.DEADLINE,
    }
    assert all(shot.media_mode == MediaMode.STATIC for shot in static_shots)
    assert all(shot.preserve_source_frame is False for shot in static_shots)
    validate_critical_information_coverage(script, units)
    validate_visual_critical_information_coverage(_plan(script, shots), script)


def test_hybrid_exact_take_preserves_the_original_source_page(tmp_path: Path) -> None:
    units = audit_critical_information(_document(tmp_path))
    script = _script(units)
    shots = compile_shots(
        VisualScenePlan(
            scene_number=1,
            source_slide_numbers=[4],
            media_mode=MediaMode.VIDEO,
            prompt="Employee submits a reimbursement request.",
        ),
        script.scenes[0],
        32,
        preserve_exact_source_frame=True,
    )

    exact = next(shot for shot in shots if shot.locked_static)
    assert exact.source_slide_number == 4
    assert exact.preserve_source_frame is True


def test_whiteboard_uses_static_cards_for_exact_information(tmp_path: Path) -> None:
    units = audit_critical_information(_document(tmp_path))
    script = _script(units)
    shots = compile_whiteboard_shots(
        VisualScenePlan(
            scene_number=1,
            source_slide_numbers=[4],
            media_mode=MediaMode.VIDEO,
            prompt="Black marker reimbursement flow.",
        ),
        script.scenes[0],
        32,
        maximum_shot_seconds=4,
    )

    assert any(shot.locked_static for shot in shots)
    assert all(
        not shot.preserve_source_frame for shot in shots if shot.locked_static
    )


def test_information_card_renders_exact_rows_without_ai(tmp_path: Path) -> None:
    units = audit_critical_information(_document(tmp_path))
    approval = [
        unit for unit in units if unit.kind == CriticalInformationKind.APPROVAL_MATRIX
    ]
    output = tmp_path / "card.png"

    artifact = render_information_card(
        approval,
        output,
        scene_number=2,
        shot_number=3,
        production_mode=ProductionMode.CORPORATE_TRAINING,
        brand=BrandKit(),
    )

    assert artifact.locked_static is True
    assert artifact.path == output
    with Image.open(output) as image:
        assert image.size == (1920, 1080)
