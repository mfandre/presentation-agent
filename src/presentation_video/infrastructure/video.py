from __future__ import annotations

import logging
from pathlib import Path

from presentation_video.domain.models import (
    AudioArtifact,
    SceneArtifact,
    SlideContent,
    VisualArtifact,
)
from presentation_video.domain.ports import SceneRenderer, VideoAssembler
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.speech import media_duration

logger = logging.getLogger(__name__)


class FfmpegSceneRenderer(SceneRenderer):
    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30) -> None:
        self._width = width
        self._height = height
        self._fps = fps

    async def render(
        self,
        scene_number: int,
        source_slide: SlideContent,
        audio: AudioArtifact,
        output_path: Path,
        presenter_video: Path | None = None,
        visual: VisualArtifact | None = None,
    ) -> SceneArtifact:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        base_filter = (
            f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease,"
            f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        )
        target_duration = max(audio.duration_seconds, 0.1)

        visual = visual or VisualArtifact(
            scene_number=scene_number,
            path=source_slide.image_path,
            kind="image",
        )
        if visual.kind == "video":
            source_duration = await media_duration(visual.path)
            finite_video_filter = (
                f"{base_filter},trim=duration={target_duration:.3f},setpts=PTS-STARTPTS"
            )
            logger.info(
                "scene visual looped at original speed scene=%s source_duration_seconds=%.3f "
                "target_duration_seconds=%.3f estimated_repetitions=%.2f",
                scene_number,
                source_duration,
                target_duration,
                target_duration / source_duration,
            )
        else:
            finite_video_filter = (
                f"{base_filter},trim=duration={target_duration:.3f},setpts=PTS-STARTPTS"
            )
        if visual.kind == "video" and presenter_video is None:
            await run_process(
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(visual.path),
                "-i",
                str(audio.path),
                "-vf",
                finite_video_filter,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-r",
                str(self._fps),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-t",
                f"{target_duration:.3f}",
                "-movflags",
                "+faststart",
                str(output_path),
            )
        elif presenter_video is None:
            await run_process(
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-framerate",
                str(self._fps),
                "-i",
                str(visual.path),
                "-i",
                str(audio.path),
                "-vf",
                base_filter,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-tune",
                "stillimage",
                "-r",
                str(self._fps),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-t",
                f"{target_duration:.3f}",
                "-movflags",
                "+faststart",
                str(output_path),
            )
        else:
            filter_complex = (
                f"[0:v]{finite_video_filter}[slide];"
                f"[1:v]scale=420:-2,format=rgba,tpad=stop_mode=clone:"
                f"stop_duration={target_duration + 1:.3f},trim=duration={target_duration:.3f},"
                "setpts=PTS-STARTPTS[presenter];"
                "[slide][presenter]overlay=W-w-60:H-h-40:shortest=0:eof_action=pass[outv]"
            )
            visual_input_args = (
                ["-stream_loop", "-1", "-i", str(visual.path)]
                if visual.kind == "video"
                else ["-loop", "1", "-framerate", str(self._fps), "-i", str(visual.path)]
            )
            await run_process(
                "ffmpeg",
                "-y",
                *visual_input_args,
                "-i",
                str(presenter_video),
                "-i",
                str(audio.path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-map",
                "2:a:0",
                "-c:v",
                "libx264",
                "-r",
                str(self._fps),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-t",
                f"{target_duration:.3f}",
                "-movflags",
                "+faststart",
                str(output_path),
            )

        return SceneArtifact(
            scene_number=scene_number,
            path=output_path,
            duration_seconds=await media_duration(output_path),
        )


class FfmpegVideoAssembler(VideoAssembler):
    async def assemble(self, scenes: list[SceneArtifact], output_path: Path) -> float:
        if not scenes:
            raise ValueError("At least one scene is required")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_file = output_path.with_suffix(".concat.txt")
        concat_file.write_text(
            "\n".join(f"file '{scene.path.resolve().as_posix()}'" for scene in scenes),
            encoding="utf-8",
        )
        await run_process(
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_path),
        )
        return await media_duration(output_path)
