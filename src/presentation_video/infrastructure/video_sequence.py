from __future__ import annotations

from pathlib import Path

from presentation_video.domain.models import VisualArtifact
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.speech import media_duration


async def compose_shot_clips(
    scene_number: int,
    shots: list[tuple[VisualArtifact, float]],
    output_path: Path,
) -> VisualArtifact:
    """Normalize and concatenate short generated clips into one scene visual."""

    if not shots:
        raise ValueError(f"scene {scene_number} has no generated shots")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_args: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, (artifact, duration) in enumerate(shots):
        if not artifact.path.is_file() or artifact.path.stat().st_size == 0:
            raise ValueError(
                f"visual QA failed for scene {scene_number}, shot {artifact.shot_number}"
            )
        actual_duration = await media_duration(artifact.path)
        if actual_duration <= 0:
            raise ValueError(
                f"visual QA found an empty clip for scene {scene_number}, "
                f"shot {artifact.shot_number}"
            )
        input_args.extend(["-i", str(artifact.path)])
        filters.append(
            f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
            f"trim=duration={min(duration, actual_duration):.3f},"
            "setpts=PTS-STARTPTS,fps=30,setsar=1,format=yuv420p"
            f"[shot{index}]"
        )
        labels.append(f"[shot{index}]")
    filters.append(f"{''.join(labels)}concat=n={len(shots)}:v=1:a=0[outv]")
    await run_process(
        "ffmpeg",
        "-y",
        *input_args,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        str(output_path),
    )
    return VisualArtifact(
        scene_number=scene_number,
        path=output_path,
        kind="video",
    )
