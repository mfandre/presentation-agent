import wave
from pathlib import Path

import pytest

from presentation_video.application.pipeline import (
    CreatePresentationVideo,
    _cinematic_script,
    _cinematic_visual_plan,
    _valid_wav_duration,
)
from presentation_video.domain.models import (
    AudioArtifact,
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
