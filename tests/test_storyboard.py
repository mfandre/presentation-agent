from __future__ import annotations

from pathlib import Path
import pytest
from PIL import Image, ImageDraw
import presentation_video.application.storyboard as storyboard_module

from presentation_video.application.storyboard import (
    _trim_storyboard_cell,
    build_segment_storyboard,
    character_reference_prompt,
    compact_storyboard_sheet_prompt,
    compile_storyboard_panels,
    generate_character_references,
    generate_storyboard_bundle,
    storyboard_sheet_prompt,
)
from presentation_video.domain.errors import VisualSafetyBlockedError
from presentation_video.domain.models import (
    CharacterProfile,
    CreativeDirection,
    MediaMode,
    PresentationScript,
    PresentationVisualPlan,
    SceneScript,
    SlideContent,
    VisualArtifact,
    VisualGenerationPurpose,
    VisualScenePlan,
    VisualShotPlan,
)
from presentation_video.infrastructure.visual_media import ReplicateImageAssetGenerator


class StoryboardImageGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple[VisualGenerationPurpose, list[str]]] = []

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        self.calls.append((plan.generation_purpose, [slide.title for slide in source_slides]))
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"scene-{plan.scene_number}-shot-{plan.shot_number}.png"
        Image.new("RGB", (1600, 900), (80, 110, 140)).save(path)
        return VisualArtifact(
            scene_number=plan.scene_number,
            shot_number=plan.shot_number,
            path=path,
            kind="image",
            revision=revision,
        )


class SafetyRetryStoryboardImageGenerator(StoryboardImageGenerator):
    def __init__(self) -> None:
        super().__init__()
        self.storyboard_prompts: list[str] = []
        self.block_next_storyboard = True

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        if plan.generation_purpose == VisualGenerationPurpose.STORYBOARD:
            self.storyboard_prompts.append(plan.prompt)
            if self.block_next_storyboard:
                self.block_next_storyboard = False
                raise VisualSafetyBlockedError(
                    "Vertex",
                    ["FinishReason.IMAGE_PROHIBITED_CONTENT"],
                )
        return await super().generate(plan, source_slides, output_dir, revision)


def _character() -> CharacterProfile:
    return CharacterProfile(
        id="lina",
        narrative_role="engenheira que conduz a transformação",
        physical_appearance="mulher de 38 anos, rosto oval e cabelos castanhos cacheados",
        wardrobe="macacão verde escuro e botas de trabalho",
        identity_markers=["pequena mecha grisalha", "óculos redondos"],
    )


def _visual_plan() -> PresentationVisualPlan:
    return PresentationVisualPlan(
        creative_direction=CreativeDirection(
            throughline="da dependência central à autonomia",
            visual_motif="documentário industrial cinematográfico",
            recurring_visual_principle="cada ação deixa um estado visível para a seguinte",
            characters=[_character()],
        ),
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                prompt="Lina inspeciona o mecanismo",
                media_mode=MediaMode.VIDEO,
                preserve_source_frame=False,
                recurring_character_ids=["lina"],
                shots=[
                    VisualShotPlan(
                        shot_number=1,
                        start_seconds=0,
                        duration_seconds=3,
                        narration_excerpt="Lina encontra o mecanismo central.",
                        story_function="establish_context",
                        prompt="Plano geral: Lina encontra o mecanismo central parado.",
                        continuity_in="porta da oficina fechada",
                        continuity_out="Lina ao lado do mecanismo",
                    ),
                    VisualShotPlan(
                        shot_number=2,
                        start_seconds=3,
                        duration_seconds=3,
                        narration_excerpt="Ela aciona máquinas autônomas.",
                        story_function="reveal",
                        prompt="Plano médio: Lina aciona a primeira máquina autônoma.",
                        continuity_in="Lina ao lado do mecanismo",
                        continuity_out="primeira máquina autônoma em movimento",
                    ),
                ],
            )
        ],
    )


def _script() -> PresentationScript:
    return PresentationScript(
        title="Transformação",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="Lina encontra o mecanismo central e aciona máquinas autônomas.",
                target_seconds=6,
                media_mode=MediaMode.VIDEO,
            )
        ],
        total_estimated_seconds=6,
    )


def test_character_reference_prompt_locks_identity_without_layout_ambiguity() -> None:
    prompt = character_reference_prompt(_character(), "cinematic realistic")

    assert "exactly one recurring fictional character" in prompt
    assert "straight front view" in prompt
    assert "left profile at exactly 90 degrees" in prompt
    assert "No text, labels, logos" in prompt
    assert "óculos redondos" in prompt


def test_storyboard_sheet_locks_selected_style_without_replacing_visual_motif() -> None:
    plan = _visual_plan()
    selected_style = "LOCKED VISUAL STYLE FOR THE ENTIRE FILM: cinematic anime"
    plan.scenes[0].visual_style = selected_style
    panels = compile_storyboard_panels(plan)
    for cell_number, panel in enumerate(panels, start=1):
        panel.cell_number = cell_number

    prompt = storyboard_sheet_prompt(
        panels,
        plan,
        _script(),
        rows=2,
        columns=2,
    )

    assert f"LOCKED ART STYLE shared by every cell: {selected_style}" in prompt
    assert "Visual motif: documentário industrial cinematográfico" in prompt


@pytest.mark.asyncio
async def test_storyboard_generates_character_first_then_clean_sheet_and_panel_fallbacks(
    tmp_path: Path,
) -> None:
    generator = StoryboardImageGenerator()
    semaphore = __import__("asyncio").Semaphore(1)
    plan = _visual_plan()

    references = await generate_character_references(
        plan,
        generator,
        tmp_path / "characters",
        semaphore,
    )
    assert plan.scenes[0].visual_style in references[0].prompt
    bundle, panels = await generate_storyboard_bundle(
        plan,
        _script(),
        references,
        generator,
        tmp_path / "storyboard",
        semaphore,
    )

    assert [reference.character_id for reference in references] == ["lina"]
    assert [call[0] for call in generator.calls] == [
        VisualGenerationPurpose.CHARACTER_REFERENCE,
        VisualGenerationPurpose.STORYBOARD,
    ]
    assert generator.calls[1][1] == ["Character identity reference — lina"]
    assert bundle.sheets[0].clean_path.is_file()
    assert bundle.sheets[0].review_path.is_file()
    assert bundle.plan_path.is_file()
    assert [(panel.scene_number, panel.shot_number) for panel in panels] == [(1, 1), (1, 2)]
    with Image.open(panels[0].path) as panel_image:
        assert panel_image.size == (1280, 720)

    segment = build_segment_storyboard(panels, tmp_path / "segment.jpg")
    assert segment.path.is_file()
    with Image.open(segment.path) as segment_image:
        assert segment_image.size == (1280, 720)

    cached_references = await generate_character_references(
        plan,
        generator,
        tmp_path / "characters",
        semaphore,
    )
    await generate_storyboard_bundle(
        plan,
        _script(),
        cached_references,
        generator,
        tmp_path / "storyboard",
        semaphore,
    )
    assert len(generator.calls) == 2


@pytest.mark.asyncio
async def test_storyboard_regenerates_cached_sheet_when_ocr_detects_visible_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = StoryboardImageGenerator()
    semaphore = __import__("asyncio").Semaphore(1)
    output_dir = tmp_path / "storyboard"
    plan = _visual_plan()

    await generate_storyboard_bundle(
        plan,
        _script(),
        [],
        generator,
        output_dir,
        semaphore,
    )
    assert len(generator.calls) == 1

    monkeypatch.setattr(
        storyboard_module,
        "_storyboard_text_evidence",
        lambda path: ["text:generated subtitle"] if path.parent.name == "clean" else [],
    )
    await generate_storyboard_bundle(
        plan,
        _script(),
        [],
        generator,
        output_dir,
        semaphore,
    )

    assert len(generator.calls) == 2
    assert "zero outer margin" in (output_dir / "clean" / "storyboard-001.prompt.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_storyboard_retries_only_blocked_sheet_with_compact_benign_prompt(
    tmp_path: Path,
) -> None:
    generator = SafetyRetryStoryboardImageGenerator()
    semaphore = __import__("asyncio").Semaphore(1)
    plan = _visual_plan()
    plan.creative_direction.characters = []
    plan.scenes[0].recurring_character_ids = []
    plan.scenes[0].shots[0].narration_excerpt = "O monstro aparece na escuridão"
    script = _script()
    script.scenes[0].narration = "O brinquedo sente medo, mas o monstro era um amigo preso."

    await generate_storyboard_bundle(
        plan,
        script,
        [],
        generator,
        tmp_path / "storyboard",
        semaphore,
    )

    assert len(generator.storyboard_prompts) == 2
    assert "clearly benign, non-graphic story" in generator.storyboard_prompts[1]
    assert "monstro" not in generator.storyboard_prompts[1].casefold()
    assert "escuridão" not in generator.storyboard_prompts[1].casefold()
    clean_dir = tmp_path / "storyboard" / "clean"
    assert (clean_dir / "storyboard-001.attempt-1.prompt.txt").is_file()
    assert (clean_dir / "storyboard-001.attempt-2.prompt.txt").is_file()
    assert (clean_dir / "storyboard-001.prompt.txt").read_text(encoding="utf-8") == (
        generator.storyboard_prompts[1]
    )

    await generate_storyboard_bundle(
        plan,
        script,
        [],
        generator,
        tmp_path / "storyboard",
        semaphore,
    )
    assert len(generator.storyboard_prompts) == 2


@pytest.mark.asyncio
async def test_long_toy_storyboard_uses_compact_text_free_prompt_before_provider_call(
    tmp_path: Path,
) -> None:
    generator = SafetyRetryStoryboardImageGenerator()
    generator.block_next_storyboard = False
    plan = _visual_plan()
    plan.creative_direction.characters[
        0
    ].physical_appearance = "small engineer toy with round glasses"
    plan.creative_direction.visual_motif = "warm cinematic toy story"
    plan.scenes[0].shots[0].prompt = "repeated production direction " * 1_200

    await generate_storyboard_bundle(
        plan,
        _script(),
        [],
        generator,
        tmp_path / "storyboard",
        __import__("asyncio").Semaphore(1),
    )

    assert len(generator.storyboard_prompts) == 1
    assert "zero gutters, zero padding, and zero borders" in generator.storyboard_prompts[0]
    assert len(generator.storyboard_prompts[0]) < 10_000


def test_compact_storyboard_prompt_omits_narration_timecodes_brand_names_and_hex_colors() -> None:
    plan = _visual_plan()
    plan.scenes[
        0
    ].visual_style = "Pixar-style 3D animation. Brand direction: palette #ff4013 and #008cb4"
    panels = compile_storyboard_panels(plan)
    for cell_number, panel in enumerate(panels, start=1):
        panel.cell_number = cell_number
    script = _script()
    script.scenes[0].narration = "NARRATION_SENTINEL that must never become a subtitle"

    prompt = compact_storyboard_sheet_prompt(
        panels,
        plan,
        script,
        rows=2,
        columns=2,
    )

    assert "NARRATION_SENTINEL" not in prompt
    assert "time " not in prompt.casefold()
    assert "Pixar" not in prompt
    assert "#ff4013" not in prompt
    assert "zero outer margin" in prompt


def test_storyboard_cell_trim_removes_neutral_margin_and_black_frame() -> None:
    cell = Image.new("RGB", (1000, 600), "#E5E7EB")
    framed = Image.new("RGB", (880, 500), "black")
    content = Image.new("RGB", (864, 484), (30, 80, 140))
    for x in range(0, content.width, 24):
        ImageDraw.Draw(content).rectangle((x, 0, x + 8, content.height), fill=(220, 120, 40))
    framed.paste(content, (8, 8))
    cell.paste(framed, (60, 50))

    trimmed = _trim_storyboard_cell(cell)

    assert trimmed.width < cell.width * 0.95
    assert trimmed.height < cell.height * 0.95
    assert trimmed.getpixel((0, trimmed.height // 2)) != (229, 231, 235)


class ReplicateImageClient:
    def __init__(self) -> None:
        self.inputs: dict[str, object] = {}

    async def run(self, _model: str, inputs: dict[str, object]) -> str:
        self.inputs = inputs
        return "https://example.test/result.png"

    @staticmethod
    def output_url(_output: object) -> str:
        return "https://example.test/result.png"

    @staticmethod
    async def download(_url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1600, 900), "grey").save(destination)


@pytest.mark.asyncio
async def test_replicate_storyboard_sends_character_sheets_as_input_images(
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "lina.png"
    Image.new("RGB", (64, 64), "grey").save(reference_path)
    client = ReplicateImageClient()
    generator = ReplicateImageAssetGenerator(
        client,  # type: ignore[arg-type]
        "openai/gpt-image-2",
        reference_input_key="input_images",
    )
    plan = VisualScenePlan(
        scene_number=1,
        generation_purpose=VisualGenerationPurpose.STORYBOARD,
        prompt="Create a clean two-panel storyboard",
        media_mode=MediaMode.VIDEO,
        preserve_source_frame=False,
    )

    await generator.generate(
        plan,
        [
            SlideContent(
                number=1,
                title="Character identity reference — lina",
                image_path=reference_path,
            )
        ],
        tmp_path / "output",
    )

    input_images = client.inputs["input_images"]
    assert isinstance(input_images, list)
    assert len(input_images) == 1
    assert str(input_images[0]).startswith("data:image/png;base64,")
    assert "clean professional" not in str(client.inputs["prompt"])
    assert "benign pre-production storyboard" in str(client.inputs["prompt"])
