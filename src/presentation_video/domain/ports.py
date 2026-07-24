from __future__ import annotations

from pathlib import Path
from typing import Protocol

from presentation_video.domain.models import (
    AudioArtifact,
    JobStatus,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    SceneArtifact,
    SlideContent,
    VisualArtifact,
    VisualScenePlan,
)


class DocumentIngestor(Protocol):
    async def ingest(self, source: Path, work_dir: Path) -> PresentationDocument: ...


class DocumentIngestorFactory(Protocol):
    def create(self, source: Path) -> DocumentIngestor: ...


class NarrativeGenerator(Protocol):
    async def generate(
        self,
        document: PresentationDocument,
        target_seconds: int,
        language: str,
        audience: str,
        tone: str,
    ) -> PresentationScript: ...


class SpeechSynthesizer(Protocol):
    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
    ) -> AudioArtifact: ...


class VisualPlanner(Protocol):
    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan: ...


class VisualAssetGenerator(Protocol):
    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact: ...


class VideoClipGenerator(Protocol):
    async def animate(
        self,
        plan: VisualScenePlan,
        image: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
    ) -> VisualArtifact: ...


class AvatarRenderer(Protocol):
    async def render(
        self,
        avatar_reference: Path | None,
        audio: AudioArtifact,
        output_path: Path,
    ) -> Path | None: ...


class SceneRenderer(Protocol):
    async def render(
        self,
        scene_number: int,
        source_slide: SlideContent,
        audio: AudioArtifact,
        output_path: Path,
        presenter_video: Path | None = None,
        visual: VisualArtifact | None = None,
    ) -> SceneArtifact: ...


class VideoAssembler(Protocol):
    async def assemble(self, scenes: list[SceneArtifact], output_path: Path) -> float: ...


class JobReporter(Protocol):
    async def update(self, job_id: str, status: JobStatus, detail: str = "") -> None: ...
