from pathlib import Path

from presentation_video.application.brand import apply_brand_kit
from presentation_video.application.visual_checkpoints import select_source_slide
from presentation_video.domain.models import (
    BrandAssetKind,
    BrandKit,
    MediaMode,
    PreparedVideoJob,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    SceneScript,
    SlideContent,
    VideoJobRequest,
    VisualArtifact,
    VisualScenePlan,
    VisualShotPlan,
)
from presentation_video.infrastructure.brand_kit import FileBrandKitRepository


def test_brand_kit_repository_versions_config_and_assets(tmp_path: Path) -> None:
    repository = FileBrandKitRepository(tmp_path / "brand")
    initial = repository.get()

    configured = repository.update(
        initial.model_copy(update={"primary_color": "#112233"})
    )
    with_logo = repository.save_asset(
        BrandAssetKind.LOGO, b"<svg/>", ".svg"
    )

    assert configured.version == initial.version + 1
    assert with_logo.version == configured.version + 1
    assert with_logo.primary_color == "#112233"
    assert with_logo.logo_path is not None
    assert with_logo.logo_path.read_bytes() == b"<svg/>"
    assert repository.get() == with_logo


def test_brand_kit_is_applied_without_adding_logo_to_scene_prompt() -> None:
    plan = PresentationVisualPlan(
        scenes=[VisualScenePlan(scene_number=1, prompt="A concrete process")]
    )
    branded = apply_brand_kit(
        plan,
        BrandKit(
            primary_color="#112233",
            secondary_color="#445566",
            accent_color="#778899",
            background_color="#FAFAFA",
            visual_style="warm editorial illustration",
        ),
    )

    assert branded.creative_direction.palette == [
        "#112233",
        "#445566",
        "#778899",
        "#FAFAFA",
    ]
    assert "warm editorial illustration" in branded.scenes[0].visual_style
    assert "do not add a logo" in branded.scenes[0].visual_style


def test_selecting_slide_for_one_video_take_does_not_change_whole_scene(
    tmp_path: Path,
) -> None:
    prepared = _prepared_job(tmp_path, with_shots=True)

    replacement = select_source_slide(prepared, 1, 1, 2)

    assert replacement.path.name == "page-2.png"
    assert replacement.source_slide_number == 2
    assert prepared.visual_plan.scenes[0].preserve_source_frame is False
    assert prepared.visual_images[1].source_slide_number is None


def test_selecting_slide_for_static_scene_records_explicit_source(
    tmp_path: Path,
) -> None:
    prepared = _prepared_job(tmp_path, with_shots=False)

    select_source_slide(prepared, 1, 1, 2)

    scene = prepared.visual_plan.scenes[0]
    assert scene.preserve_source_frame is True
    assert scene.source_slide_number == 2


def _prepared_job(tmp_path: Path, *, with_shots: bool) -> PreparedVideoJob:
    pages = []
    for number in (1, 2):
        path = tmp_path / f"page-{number}.png"
        path.write_bytes(f"page {number}".encode())
        pages.append(
            SlideContent(number=number, title=f"Page {number}", image_path=path)
        )
    scene = SceneScript(
        scene_number=1,
        source_slide_numbers=[1, 2],
        narration="A short explanation.",
        target_seconds=8,
    )
    shots = (
        [
            VisualShotPlan(
                shot_number=number,
                start_seconds=(number - 1) * 4,
                duration_seconds=4,
                narration_excerpt="A short explanation.",
                story_function="development",
                prompt=f"Take {number}",
            )
            for number in (1, 2)
        ]
        if with_shots
        else []
    )
    plan = VisualScenePlan(
        scene_number=1,
        prompt="Original frame",
        source_slide_numbers=[1, 2],
        media_mode=MediaMode.VIDEO if with_shots else MediaMode.STATIC,
        preserve_source_frame=False,
        shots=shots,
    )
    output = tmp_path / "output"
    output.mkdir()
    visual_plan_path = output / "visual-plan.json"
    visual_plan = PresentationVisualPlan(scenes=[plan])
    visual_plan_path.write_text(visual_plan.model_dump_json(), encoding="utf-8")
    script = PresentationScript(
        title="Test", scenes=[scene], total_estimated_seconds=8
    )
    return PreparedVideoJob(
        job_id="job",
        request=VideoJobRequest(source_path=tmp_path / "source.pdf", target_seconds=30),
        document=PresentationDocument(
            source_path=tmp_path / "source.pdf", slides=pages
        ),
        script=script,
        visual_plan=visual_plan,
        visual_images=[
            VisualArtifact(
                scene_number=1,
                shot_number=number,
                path=tmp_path / f"generated-{number}.png",
                kind="image",
            )
            for number in ((1, 2) if with_shots else (1,))
        ],
        work_dir=tmp_path,
        output_dir=output,
        script_path=output / "script.json",
        visual_plan_path=visual_plan_path,
    )
