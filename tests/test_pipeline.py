import wave
from pathlib import Path

import pytest
from PIL import Image

from presentation_video.application.pipeline import (
    CreatePresentationVideo,
    _cinematic_script,
    _cinematic_visual_plan,
    _model_adjusted_take_seconds,
    _storyboard_animation_input,
    _valid_wav_duration,
)
from presentation_video.domain.models import (
    AudioArtifact,
    BrandKit,
    CharacterProfile,
    CreativeDirection,
    JobStatus,
    MediaMode,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    ProductionMode,
    SceneArtifact,
    SceneScript,
    SlideContent,
    VideoJobRequest,
    VideoGeneratorCapabilities,
    VisualArtifact,
    VisualBeat,
    VisualBeatKind,
    VisualScenePlan,
)
from presentation_video.domain.errors import DurationReviewRequired


class FakeIngestorFactory:
    def __init__(self, document: PresentationDocument) -> None:
        self.document = document

    def create(self, source: Path) -> "FakeIngestorFactory":
        return self

    async def ingest(self, source: Path, work_dir: Path) -> PresentationDocument:
        return self.document


class FakeNarrativeGenerator:
    def __init__(self, script: PresentationScript) -> None:
        self.script = script

    async def generate(self, **kwargs: object) -> PresentationScript:
        return self.script


class FakeVisualPlanner:
    def __init__(self, plan: PresentationVisualPlan) -> None:
        self.visual_plan = plan

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        return self.visual_plan


class FakeImageGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, list[int]]] = []

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        self.calls.append(
            (plan.scene_number, plan.shot_number, [slide.number for slide in source_slides])
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"scene-{plan.scene_number}-shot-{plan.shot_number}.png"
        path.write_bytes(b"image")
        return VisualArtifact(
            scene_number=plan.scene_number,
            shot_number=plan.shot_number,
            path=path,
            kind="image",
            revision=revision,
        )


class FakeStoryboardImageGenerator(FakeImageGenerator):
    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        self.calls.append(
            (plan.scene_number, plan.shot_number, [slide.number for slide in source_slides])
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"scene-{plan.scene_number}-shot-{plan.shot_number}.png"
        Image.new("RGB", (1600, 900), "#65748A").save(path)
        return VisualArtifact(
            scene_number=plan.scene_number,
            shot_number=plan.shot_number,
            path=path,
            kind="image",
            revision=revision,
        )


class FakeSpeechSynthesizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
    ) -> AudioArtifact:
        self.calls.append(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"audio")
        return AudioArtifact(path=output_path, duration_seconds=10)


class FakeAvatarRenderer:
    async def render(
        self, avatar_reference: Path | None, audio: AudioArtifact, output_path: Path
    ) -> None:
        return None


class FakeClipGenerator:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def animate(
        self,
        plan: VisualScenePlan,
        image: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
    ) -> VisualArtifact:
        self.calls.append(plan.scene_number)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"scene-{plan.scene_number}.mp4"
        path.write_bytes(b"clip")
        return VisualArtifact(
            scene_number=plan.scene_number,
            shot_number=plan.shot_number,
            path=path,
            kind="video",
        )


class FakeStoryboardClipGenerator(FakeClipGenerator):
    def __init__(self) -> None:
        super().__init__()
        self.storyboard_calls: list[tuple[int, int]] = []

    @property
    def capabilities(self) -> VideoGeneratorCapabilities:
        return VideoGeneratorCapabilities(
            supports_storyboard_reference=True,
            supports_multishot=True,
            maximum_output_seconds=8,
            maximum_reference_images=4,
        )

    async def animate_storyboard(
        self,
        plans: list[VisualScenePlan],
        storyboard: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
        segment_number: int,
    ) -> VisualArtifact:
        self.storyboard_calls.append((len(plans), round(duration_seconds)))
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / (
            f"scene-{storyboard.scene_number:03d}-storyboard-segment-{segment_number:03d}.mp4"
        )
        path.write_bytes(b"multi-shot")
        return VisualArtifact(
            scene_number=storyboard.scene_number,
            shot_number=storyboard.shot_number,
            path=path,
            kind="video",
        )


class FakeVeoClipGenerator(FakeClipGenerator):
    @property
    def capabilities(self) -> VideoGeneratorCapabilities:
        return VideoGeneratorCapabilities(
            minimum_output_seconds=4,
            maximum_output_seconds=8,
            supports_first_frame=True,
            supports_last_frame=True,
        )


class FakeSceneRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str | None]] = []

    async def render(
        self,
        scene_number: int,
        source_slide: SlideContent,
        audio: AudioArtifact,
        output_path: Path,
        presenter_video: Path | None = None,
        visual: VisualArtifact | None = None,
        visual_image: VisualArtifact | None = None,
        visual_plan: VisualScenePlan | None = None,
    ) -> SceneArtifact:
        self.calls.append((scene_number, source_slide.number, visual.kind if visual else None))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"scene")
        return SceneArtifact(scene_number=scene_number, path=output_path, duration_seconds=10)


class FakeAssembler:
    async def assemble(self, scenes: list[SceneArtifact], output_path: Path) -> float:
        output_path.write_bytes(b"video")
        return sum(scene.duration_seconds for scene in scenes)


class FakeReporter:
    def __init__(self) -> None:
        self.updates: list[tuple[JobStatus, str]] = []

    async def update(self, job_id: str, status: JobStatus, detail: str = "") -> None:
        self.updates.append((status, detail))


@pytest.mark.asyncio
async def test_pipeline_processes_narrative_scenes_not_every_source_page(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    document = PresentationDocument(
        source_path=source,
        slides=[
            SlideContent(
                number=number,
                title=f"Page {number}",
                image_path=tmp_path / f"page-{number}.png",
            )
            for number in range(1, 6)
        ],
    )
    for slide in document.slides:
        slide.image_path.write_bytes(b"source-image")
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1, 2, 3],
                narration="Opening and context",
                target_seconds=15,
                media_mode=MediaMode.VIDEO,
                story_beat="opening",
                visual_intent="A word-free real-world opening",
                transition_to_next="Move from context into the evidence",
            ),
            SceneScript(
                scene_number=2,
                source_slide_numbers=[4, 5],
                narration="Development and conclusion",
                target_seconds=15,
                media_mode=MediaMode.STATIC,
                story_beat="conclusion",
                visual_intent="Preserve the exact conclusion",
                transition_to_next="End on the takeaway",
            ),
        ],
        total_estimated_seconds=30,
    )
    visual_plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=scene.scene_number,
                source_slide_numbers=scene.source_slide_numbers,
                prompt=f"Visual {scene.scene_number}",
                media_mode=scene.media_mode,
                source_slide_number=5 if scene.scene_number == 2 else None,
            )
            for scene in script.scenes
        ]
    )
    images = FakeImageGenerator()
    speech = FakeSpeechSynthesizer()
    clips = FakeClipGenerator()
    renderer = FakeSceneRenderer()
    pipeline = CreatePresentationVideo(
        ingestor_factory=FakeIngestorFactory(document),
        narrative_generator=FakeNarrativeGenerator(script),
        visual_planner=FakeVisualPlanner(visual_plan),
        visual_asset_generator=images,
        video_clip_generator=clips,
        speech_synthesizer=speech,
        avatar_renderer=FakeAvatarRenderer(),
        scene_renderer=renderer,
        video_assembler=FakeAssembler(),
        reporter=FakeReporter(),
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    request = VideoJobRequest(source_path=source, target_seconds=30)

    prepared = await pipeline.prepare(request, job_id="test-job")
    result = await pipeline.finalize(prepared)

    assert images.calls == [(1, 1, [1, 2, 3])]
    assert speech.calls == ["Opening and context", "Development and conclusion"]
    assert clips.calls == [1]
    assert renderer.calls == [(1, 1, "video"), (2, 5, "image")]
    assert len(prepared.visual_images) == 2
    assert prepared.visual_images[1].path == document.slides[4].image_path
    assert result.duration_seconds == 20

    prepared.visual_images[0].path.unlink()
    restored = await pipeline.restore(request, job_id="test-job")

    assert images.calls == [(1, 1, [1, 2, 3]), (1, 1, [1, 2, 3])]
    assert [image.scene_number for image in restored.visual_images] == [1, 2]


@pytest.mark.asyncio
async def test_training_pipeline_inserts_exact_approval_card_inside_dynamic_scene(
    tmp_path: Path,
) -> None:
    source = tmp_path / "policy.pdf"
    source.write_bytes(b"pdf")
    page = tmp_path / "page-4.png"
    Image.new("RGB", (1280, 720), "white").save(page)
    document = PresentationDocument(
        source_path=source,
        slides=[
            SlideContent(
                number=4,
                title="Limites e alçadas",
                body_text=(
                    "Limites e alçadas\nValor total da solicitação\nAprovação mínima\n"
                    "Até R$ 500,00\nGestor imediato\n"
                    "De R$ 500,01 a R$ 3.000,00\nGerente da área\n"
                    "De R$ 3.000,01 a R$ 10.000,00\nDiretor da área\n"
                    "Acima de R$ 10.000,00\nDiretor da área e Financeiro\n"
                    "O fracionamento para reduzir a alçada é proibido.\n"
                    "Registrar até 10 dias corridos. Aprovar em até 5 dias úteis."
                ),
                image_path=page,
            )
        ],
    )
    script = PresentationScript(
        title="Reembolso",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[4],
                narration=(
                    "Registre a solicitação no prazo correto. As alçadas de aprovação mudam "
                    "conforme o valor total e o Financeiro conclui o fluxo."
                ),
                target_seconds=32,
                media_mode=MediaMode.VIDEO,
                story_beat="Fluxo e alçadas",
                visual_intent="Colaborador registra um reembolso.",
                scene_purpose="Ensinar o processo e as alçadas",
            )
        ],
        total_estimated_seconds=32,
    )
    visual_plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[4],
                prompt="Employee submits a reimbursement request.",
                media_mode=MediaMode.VIDEO,
            )
        ]
    )
    images = FakeImageGenerator()
    pipeline = CreatePresentationVideo(
        ingestor_factory=FakeIngestorFactory(document),
        narrative_generator=FakeNarrativeGenerator(script),
        visual_planner=FakeVisualPlanner(visual_plan),
        visual_asset_generator=images,
        video_clip_generator=FakeClipGenerator(),
        speech_synthesizer=FakeSpeechSynthesizer(),
        avatar_renderer=FakeAvatarRenderer(),
        scene_renderer=FakeSceneRenderer(),
        video_assembler=FakeAssembler(),
        reporter=FakeReporter(),
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )

    prepared = await pipeline.prepare(
        VideoJobRequest(
            source_path=source,
            target_seconds=32,
            production_mode=ProductionMode.CORPORATE_TRAINING,
            brand_kit=BrandKit(),
        ),
        job_id="approval-job",
    )

    exact_images = [image for image in prepared.visual_images if image.locked_static]
    assert len(exact_images) == 1
    assert exact_images[0].path.name.endswith("-exact-r1.png")
    assert exact_images[0].path.is_file()
    assert any(shot.locked_static for shot in prepared.visual_plan.scenes[0].shots)
    assert any(not shot.locked_static for shot in prepared.visual_plan.scenes[0].shots)
    assert len(images.calls) == 1

@pytest.mark.asyncio
async def test_cinematic_preparation_generates_multiple_reviewable_shots(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    slide = SlideContent(
        number=1,
        title="Transformation",
        image_path=tmp_path / "page-1.png",
    )
    slide.image_path.write_bytes(b"source")
    document = PresentationDocument(source_path=source, slides=[slide])
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="A central engine gives way to autonomous distributed machines.",
                target_seconds=17,
                media_mode=MediaMode.STATIC,
            )
        ],
        total_estimated_seconds=17,
    )
    visual_plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                prompt="A factory transformation grounded in the source.",
                media_mode=MediaMode.STATIC,
                preserve_source_frame=True,
            )
        ]
    )
    images = FakeImageGenerator()
    pipeline = CreatePresentationVideo(
        ingestor_factory=FakeIngestorFactory(document),
        narrative_generator=FakeNarrativeGenerator(script),
        visual_planner=FakeVisualPlanner(visual_plan),
        visual_asset_generator=images,
        video_clip_generator=FakeClipGenerator(),
        speech_synthesizer=FakeSpeechSynthesizer(),
        avatar_renderer=FakeAvatarRenderer(),
        scene_renderer=FakeSceneRenderer(),
        video_assembler=FakeAssembler(),
        reporter=FakeReporter(),
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )

    prepared = await pipeline.prepare(
        VideoJobRequest(
            source_path=source,
            target_seconds=30,
            production_mode=ProductionMode.CINEMATIC_STORY,
        ),
        job_id="cinematic-job",
    )

    shots = prepared.visual_plan.scenes[0].shots
    assert len(shots) == 2
    assert all(shot.duration_seconds <= 8 for shot in shots)
    assert len(prepared.visual_images) == 2
    assert [(image.scene_number, image.shot_number) for image in prepared.visual_images] == [
        (1, 1),
        (1, 2),
    ]
    assert all(image.path != slide.image_path for image in prepared.visual_images)
    assert images.calls == [(1, 1, [1]), (1, 2, [1])]


@pytest.mark.asyncio
async def test_whiteboard_preparation_builds_model_aware_microstates(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    source_image = tmp_path / "page-1.png"
    Image.new("RGB", (1600, 900), "white").save(source_image)
    document = PresentationDocument(
        source_path=source,
        slides=[SlideContent(number=1, title="Story", image_path=source_image)],
    )
    script = PresentationScript(
        title="Whiteboard lesson",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="Uma pessoa encontra um desafio, conecta a solução e chega ao resultado.",
                target_seconds=10,
                media_mode=MediaMode.VIDEO,
            )
        ],
        total_estimated_seconds=10,
    )
    visual_plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                prompt="A cumulative whiteboard teaching illustration",
                media_mode=MediaMode.VIDEO,
                preserve_source_frame=False,
            )
        ]
    )
    pipeline = CreatePresentationVideo(
        ingestor_factory=FakeIngestorFactory(document),
        narrative_generator=FakeNarrativeGenerator(script),
        visual_planner=FakeVisualPlanner(visual_plan),
        visual_asset_generator=FakeStoryboardImageGenerator(),
        video_clip_generator=FakeClipGenerator(),
        whiteboard_video_clip_generator=FakeVeoClipGenerator(),
        speech_synthesizer=FakeSpeechSynthesizer(),
        avatar_renderer=FakeAvatarRenderer(),
        scene_renderer=FakeSceneRenderer(),
        video_assembler=FakeAssembler(),
        reporter=FakeReporter(),
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
        whiteboard_target_take_seconds=2,
    )

    prepared = await pipeline.prepare(
        VideoJobRequest(
            source_path=source,
            target_seconds=30,
            production_mode=ProductionMode.WHITEBOARD_EXPLAINER,
        ),
        job_id="whiteboard-microstates",
    )

    assert len(prepared.visual_plan.scenes[0].shots) == 3
    assert len(prepared.visual_images) == 3
    assert all(image.start_path is not None for image in prepared.visual_images)
    assert max(
        shot.duration_seconds for shot in prepared.visual_plan.scenes[0].shots
    ) <= 4


@pytest.mark.asyncio
async def test_cinematic_storyboard_route_generates_cast_and_uses_multishot_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    source_image = tmp_path / "page-1.png"
    Image.new("RGB", (1600, 900), "white").save(source_image)
    document = PresentationDocument(
        source_path=source,
        slides=[SlideContent(number=1, title="Story", image_path=source_image)],
    )
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="Lina enters the workshop and starts the autonomous machines.",
                target_seconds=10,
                media_mode=MediaMode.VIDEO,
            )
        ],
        total_estimated_seconds=10,
    )
    character = CharacterProfile(
        id="lina",
        narrative_role="lead engineer",
        physical_appearance="38-year-old woman with an oval face and curly brown hair",
        wardrobe="dark green work coveralls and round glasses",
    )
    visual_plan = PresentationVisualPlan(
        creative_direction=CreativeDirection(
            throughline="from central dependence to autonomy",
            visual_motif="cinematic industrial documentary",
            characters=[character],
        ),
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                prompt="Lina transforms the workshop",
                media_mode=MediaMode.VIDEO,
                preserve_source_frame=False,
                recurring_character_ids=["lina"],
            )
        ],
    )
    images = FakeStoryboardImageGenerator()
    clips = FakeStoryboardClipGenerator()
    reporter = FakeReporter()

    async def fake_compose(
        scene_number: int,
        artifacts: list[tuple[VisualArtifact, float]],
        output_path: Path,
    ) -> VisualArtifact:
        assert artifacts
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"sequence")
        return VisualArtifact(scene_number=scene_number, path=output_path, kind="video")

    monkeypatch.setattr(
        "presentation_video.application.pipeline._compose_shot_clips",
        fake_compose,
    )
    pipeline = CreatePresentationVideo(
        ingestor_factory=FakeIngestorFactory(document),
        narrative_generator=FakeNarrativeGenerator(script),
        visual_planner=FakeVisualPlanner(visual_plan),
        visual_asset_generator=images,
        video_clip_generator=clips,
        speech_synthesizer=FakeSpeechSynthesizer(),
        avatar_renderer=FakeAvatarRenderer(),
        scene_renderer=FakeSceneRenderer(),
        video_assembler=FakeAssembler(),
        reporter=reporter,
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
        storyboard_enabled=True,
        storyboard_panel_seconds=3,
    )

    prepared = await pipeline.prepare(
        VideoJobRequest(
            source_path=source,
            target_seconds=30,
            production_mode=ProductionMode.CINEMATIC_STORY,
        ),
        job_id="storyboard-job",
    )
    result = await pipeline.finalize(prepared)

    assert prepared.storyboard is not None
    assert [reference.character_id for reference in prepared.character_references] == ["lina"]
    assert len(prepared.visual_images) == 4
    assert len(images.calls) == 2  # one character sheet + one storyboard master
    assert clips.calls == []
    assert sorted(clips.storyboard_calls) == [(1, 2), (3, 8)]
    assert result.video_path.is_file()
    statuses = [status for status, _ in reporter.updates]
    assert JobStatus.DESIGNING_CHARACTERS in statuses
    assert JobStatus.STORYBOARDING in statuses


def test_valid_wav_duration_accepts_checkpoint_and_rejects_partial_file(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.wav"
    with wave.open(str(valid), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 16_000)
    partial = tmp_path / "partial.wav"
    partial.write_bytes(b"unfinished")

    assert _valid_wav_duration(valid) == pytest.approx(1)
    assert _valid_wav_duration(partial) is None


def test_cinematic_mode_removes_all_source_frames_and_slide_beats() -> None:
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="A transformation begins.",
                target_seconds=30,
                media_mode=MediaMode.STATIC,
            )
        ],
        total_estimated_seconds=30,
    )
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                source_slide_number=1,
                preserve_source_frame=True,
                media_mode=MediaMode.STATIC,
                prompt="Show the source page.",
                visual_beats=[
                    VisualBeat(
                        beat_number=1,
                        kind=VisualBeatKind.SOURCE_SLIDE,
                        duration_seconds=30,
                    )
                ],
            )
        ]
    )

    cinematic_script = _cinematic_script(script)
    cinematic_plan = _cinematic_visual_plan(plan)

    assert cinematic_script.scenes[0].media_mode == MediaMode.VIDEO
    assert cinematic_plan.scenes[0].media_mode == MediaMode.VIDEO
    assert cinematic_plan.scenes[0].preserve_source_frame is False
    assert cinematic_plan.scenes[0].source_slide_number is None
    assert all(
        beat.kind != VisualBeatKind.SOURCE_SLIDE for beat in cinematic_plan.scenes[0].visual_beats
    )


def test_storyboard_panel_fallback_uses_next_panel_only_when_last_frame_is_supported(
    tmp_path: Path,
) -> None:
    first = VisualArtifact(scene_number=1, shot_number=1, path=tmp_path / "first.jpg", kind="image")
    second = VisualArtifact(
        scene_number=1,
        shot_number=2,
        path=tmp_path / "second.jpg",
        kind="image",
    )

    interpolated = _storyboard_animation_input(
        first,
        second,
        storyboard_present=True,
        supports_last_frame=True,
    )
    unsupported = _storyboard_animation_input(
        first,
        second,
        storyboard_present=True,
        supports_last_frame=False,
    )

    assert interpolated.start_path == first.path
    assert interpolated.path == second.path
    assert unsupported.start_path is None
    assert unsupported.path == first.path


def test_whiteboard_take_duration_is_clamped_to_the_model_minimum() -> None:
    class FourSecondGenerator(FakeClipGenerator):
        @property
        def capabilities(self) -> VideoGeneratorCapabilities:
            return VideoGeneratorCapabilities(
                minimum_output_seconds=4,
                maximum_output_seconds=8,
                supports_last_frame=True,
            )

    assert _model_adjusted_take_seconds(FourSecondGenerator(), 2, 8) == 4


@pytest.mark.asyncio
async def test_duration_checkpoint_pauses_before_speech_and_can_summarize(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    slide_paths = [tmp_path / "page-1.png", tmp_path / "page-2.png"]
    for path in slide_paths:
        path.write_bytes(b"source")
    document = PresentationDocument(
        source_path=source,
        slides=[
            SlideContent(number=index, title=f"Page {index}", image_path=path)
            for index, path in enumerate(slide_paths, start=1)
        ],
    )
    long_text = " ".join(f"palavra{index}" for index in range(100))
    script = PresentationScript(
        title="Long story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration=long_text,
                target_seconds=15,
                media_mode=MediaMode.VIDEO,
            ),
            SceneScript(
                scene_number=2,
                source_slide_numbers=[2],
                narration=long_text,
                target_seconds=15,
                media_mode=MediaMode.STATIC,
            ),
        ],
        total_estimated_seconds=30,
    )
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                prompt="Generated visual",
                media_mode=MediaMode.VIDEO,
                preserve_source_frame=False,
            ),
            VisualScenePlan(
                scene_number=2,
                source_slide_numbers=[2],
                source_slide_number=2,
                prompt="Preserved information",
                media_mode=MediaMode.STATIC,
                preserve_source_frame=True,
            ),
        ]
    )
    speech = FakeSpeechSynthesizer()
    pipeline = CreatePresentationVideo(
        ingestor_factory=FakeIngestorFactory(document),
        narrative_generator=FakeNarrativeGenerator(script),
        visual_planner=FakeVisualPlanner(plan),
        visual_asset_generator=FakeImageGenerator(),
        video_clip_generator=FakeClipGenerator(),
        speech_synthesizer=speech,
        avatar_renderer=FakeAvatarRenderer(),
        scene_renderer=FakeSceneRenderer(),
        video_assembler=FakeAssembler(),
        reporter=FakeReporter(),
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    request = VideoJobRequest(source_path=source, target_seconds=30)

    with pytest.raises(DurationReviewRequired) as review:
        await pipeline.prepare(request, job_id="duration-job")

    assert review.value.estimated_seconds > 30
    assert speech.calls == []

    prepared = await pipeline.prepare(
        request,
        job_id="duration-job",
        duration_decision="summarize",
    )

    assert sum(len(scene.narration.split()) for scene in prepared.script.scenes) <= 77
    assert prepared.script.total_estimated_seconds == 30
    assert len(speech.calls) == 2
