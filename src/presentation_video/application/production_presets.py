from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from presentation_video.application.production_policy import (
    enforce_cinematic_script,
    enforce_cinematic_visual_plan,
    validate_cinematic_has_no_source_frames,
)
from presentation_video.application.instructional_design import (
    direct_corporate_training_plan,
    validate_corporate_training_plan,
)
from presentation_video.domain.models import (
    MediaMode,
    MotionPreset,
    PresentationScript,
    PresentationVisualPlan,
    ProductionMode,
    TransitionPreset,
)


class PresetChoice(BaseModel):
    value: str
    label: str


class PresetOption(BaseModel):
    id: str
    label: str
    type: Literal["select"]
    default: str
    choices: list[PresetChoice]


class ProductionPreset(BaseModel):
    id: ProductionMode
    label: str
    description: str
    icon: str
    strategy: Literal["hybrid", "cinematic", "whiteboard", "training"]
    narrative_direction: str = ""
    options: list[PresetOption] = Field(default_factory=list)


_PRESETS = (
    ProductionPreset(
        id=ProductionMode.HYBRID_PRESENTATION,
        label="Apresentação híbrida",
        description=(
            "Combina cenas geradas com páginas fixas quando números, tabelas ou "
            "diagramas precisam ser lidos."
        ),
        icon="presentation",
        strategy="hybrid",
    ),
    ProductionPreset(
        id=ProductionMode.CINEMATIC_STORY,
        label="História cinematográfica",
        description=(
            "Cria imagens e cenas originais do início ao fim, sem mostrar slides, "
            "páginas ou documentos fixos."
        ),
        icon="film",
        strategy="cinematic",
        narrative_direction=(
            "Build a continuous cinematic story from beginning to end, with a strong "
            "hook, escalating development, recurring visual motifs, meaningful "
            "transitions, and a clear resolution."
        ),
    ),
    ProductionPreset(
        id=ProductionMode.WHITEBOARD_EXPLAINER,
        label="Whiteboard explicativo",
        description=(
            "Redesenha o conteúdo como ilustrações didáticas, diagramas e ícones "
            "traçados em um quadro branco."
        ),
        icon="pen-tool",
        strategy="whiteboard",
        narrative_direction=(
            "Write a conversational educational explainer with an immediate curiosity "
            "hook, a logical progression, concrete comparisons, and a memorable final "
            "takeaway. Narration only; no presenter or character dialogue."
        ),
        options=[
            PresetOption(
                id="hand_style",
                label="Animação do desenho",
                type="select",
                default="marker_only",
                choices=[
                    PresetChoice(value="marker_only", label="Somente efeito de desenho"),
                    PresetChoice(value="visible_hand", label="Mão e marcador visíveis"),
                ],
            ),
            PresetOption(
                id="accent_color",
                label="Uso de cor",
                type="select",
                default="blue",
                choices=[
                    PresetChoice(value="blue", label="Preto + azul"),
                    PresetChoice(value="none", label="Somente preto e branco"),
                    PresetChoice(value="brand", label="Preto + cor da apresentação"),
                ],
            ),
            PresetOption(
                id="pacing",
                label="Ritmo visual",
                type="select",
                default="didactic",
                choices=[
                    PresetChoice(value="didactic", label="Didático"),
                    PresetChoice(value="dynamic", label="Dinâmico"),
                    PresetChoice(value="summary", label="Resumo rápido"),
                ],
            ),
        ],
    ),
    ProductionPreset(
        id=ProductionMode.CORPORATE_TRAINING,
        label="Treinamento corporativo",
        description=(
            "Escolhe por cena entre exemplos realistas, fluxos explicativos e páginas "
            "fiéis para regras, prazos e procedimentos."
        ),
        icon="graduation-cap",
        strategy="training",
        narrative_direction=(
            "Write an instructional corporate training narrative. State observable learning "
            "objectives, explain one idea at a time, use concrete workplace examples, reinforce "
            "rules without merely reading them, and finish with a concise practical recap."
        ),
    ),
)

_REGISTRY = {preset.id: preset for preset in _PRESETS}


def list_production_presets() -> list[ProductionPreset]:
    return [preset.model_copy(deep=True) for preset in _PRESETS]


def get_production_preset(mode: ProductionMode) -> ProductionPreset:
    return _REGISTRY[mode]


def direct_narrative_tone(mode: ProductionMode, tone: str) -> str:
    direction = get_production_preset(mode).narrative_direction
    return f"{tone}. {direction}" if direction else tone


def transform_script(mode: ProductionMode, script: PresentationScript) -> PresentationScript:
    if get_production_preset(mode).strategy == "cinematic":
        return enforce_cinematic_script(script)
    return script


def transform_visual_plan(
    mode: ProductionMode,
    plan: PresentationVisualPlan,
    options: dict[str, str] | None = None,
    script: PresentationScript | None = None,
) -> PresentationVisualPlan:
    strategy = get_production_preset(mode).strategy
    if strategy == "cinematic":
        return enforce_cinematic_visual_plan(plan)
    if strategy == "training":
        if script is None:
            raise ValueError("corporate training visual direction requires the narrative script")
        return direct_corporate_training_plan(plan, script)
    if strategy != "whiteboard":
        return plan

    selected = options or {}
    hand = (
        "A natural hand holding a black marker must remain visible and draw every new element. "
        if selected.get("hand_style") == "visible_hand"
        else "Use a progressive marker draw-on effect; do not show a hand. "
    )
    # A monochrome target gives the video model an unambiguous set of strokes to trace.
    # Accent colors encourage it to interpret the composition and invent transitional marks.
    color = "Use one matte black marker only; every line must be solid black. "
    pacing = {
        "dynamic": "Reveal elements briskly with energetic arrows and concise transitions. ",
        "summary": "Keep each composition extremely concise, showing only the essential takeaway. ",
    }.get(
        selected.get("pacing"),
        "Reveal one teaching idea at a time at a calm, easy-to-follow pace. ",
    )
    scenes = []
    for scene in plan.scenes:
        style = (
            "Classic educational whiteboard animation. Pure white background, clean hand-sketched "
            "black marker line art, simple doodles, icons, arrows, unlabeled charts and diagrams. "
            "Strictly pictorial: every marker stroke must form a shape, object, icon, arrow, chart "
            "mark, or diagram connection—never handwriting or typography. "
            f"{color}{hand}{pacing}"
        )
        prompt = (
            f"{style} Teaching goal: {scene.scene_purpose or scene.story_beat}. "
            f"Draw this source-grounded concept: {scene.prompt} "
            "Arrange the teaching elements in clear narration order from left to right, using "
            "distinct visual zones with generous whitespace so the composition can be revealed "
            "cumulatively in several easy-to-follow drawing stages. "
            "Show how the illustration is progressively constructed and finish with a visual "
            "bridge into the next idea. Use no labels: communicate only through recognizable "
            "symbols, shapes, scale, grouping, sequence, and spatial relationships."
        )
        negative = (
            "photorealism, cinematic environment, realistic location, 3D render, detailed scenery, "
            "real actor, presenter, colored background, glossy infographic, stock photography, "
            "handwriting, writing motion, typography, glyphs, alphabetic characters, words, "
            "letters, digits, labels, titles, captions, legends, axis labels, annotations, "
            f"{scene.negative_prompt}"
        )
        scenes.append(
            scene.model_copy(
                update={
                    "prompt": prompt,
                    "negative_prompt": negative,
                    "media_mode": MediaMode.VIDEO,
                    "source_slide_number": None,
                    "preserve_source_frame": False,
                    "visual_style": style,
                    "camera_motion": "locked overhead whiteboard view; drawing motion only",
                    "motion_preset": MotionPreset.NONE,
                    "entrance_motion": "progressive marker draw-on",
                    "focal_action": "draw and connect the teaching elements in narration order",
                    "transition_out": "erase, continue the line, or move an arrow into the next idea",
                    "transition_preset": TransitionPreset.DISSOLVE,
                    "shots": [],
                    "visual_beats": [],
                }
            )
        )
    return plan.model_copy(update={"scenes": scenes})


def validate_preset_plan(mode: ProductionMode, plan: PresentationVisualPlan) -> None:
    strategy = get_production_preset(mode).strategy
    if strategy == "cinematic":
        validate_cinematic_has_no_source_frames(plan)
    if strategy == "whiteboard":
        invalid = [
            scene.scene_number
            for scene in plan.scenes
            if scene.preserve_source_frame
            or scene.source_slide_number is not None
            or scene.media_mode != MediaMode.VIDEO
            or not scene.shots
            or any(shot.duration_seconds > 8 for shot in scene.shots)
        ]
        if invalid:
            raise ValueError(
                "whiteboard_explainer contains non-generated scenes or scenes without "
                f"bounded takes: {invalid}"
            )
    if strategy == "training":
        validate_corporate_training_plan(plan)
