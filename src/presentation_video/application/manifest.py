from __future__ import annotations

import json
from pathlib import Path

from presentation_video.domain.models import PreparedVideoJob, SceneArtifact


def write_job_manifest(
    prepared: PreparedVideoJob,
    *,
    video_path: Path,
    duration_seconds: float,
    captions_vtt_path: Path,
    captions_srt_path: Path,
    caption_cue_count: int,
    scenes: list[SceneArtifact],
) -> Path:
    manifest = {
        "job_id": prepared.job_id,
        "source": str(prepared.request.source_path),
        "video": str(video_path),
        "duration_seconds": duration_seconds,
        "target_seconds": prepared.request.target_seconds,
        "language": prepared.request.language,
        "audience": prepared.request.audience,
        "tone": prepared.request.tone,
        "production_mode": prepared.request.production_mode.value,
        "approved_images": [
            image.model_dump(mode="json") for image in prepared.visual_images
        ],
        "storyboard": [
            plan.model_dump(mode="json") for plan in prepared.visual_plan.scenes
        ],
        "captions": {
            "vtt": str(captions_vtt_path),
            "srt": str(captions_srt_path),
            "cue_count": caption_cue_count,
        },
        "scenes": [scene.model_dump(mode="json") for scene in scenes],
    }
    manifest_path = prepared.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path
