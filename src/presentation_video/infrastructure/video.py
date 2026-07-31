from __future__ import annotations

import logging
from pathlib import Path

from presentation_video.domain.models import (
    AudioArtifact,
    SceneArtifact,
    SlideContent,
    VisualArtifact,
    VisualBeatKind,
    VisualScenePlan,
)
from presentation_video.domain.ports import SceneRenderer, VideoAssembler
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.speech import media_duration
from presentation_video.infrastructure.visual_media import _local_motion_filter

logger = logging.getLogger(__name__)
BRAND_CARD_SECONDS = 3.0


async def prepend_opening_image(
    video_path: Path,
    opening_image_path: Path,
    output_path: Path,
    *,
    visible_seconds: float = BRAND_CARD_SECONDS,
) -> float:
    """Prepend a silent full-frame brand card before the narrated presentation."""
    if not video_path.is_file() or not opening_image_path.is_file():
        raise FileNotFoundError("Video and opening image must exist before prepending the card")
    if visible_seconds <= 0:
        raise ValueError("Opening image duration must be greater than zero")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
        f"trim=duration={visible_seconds:.3f},setpts=PTS-STARTPTS[openingv];"
        "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
        "setpts=PTS-STARTPTS[mainv];"
        "[openingv][mainv]concat=n=2:v=1:a=0[outv];"
        "[2:a]asetpts=PTS-STARTPTS[openinga];"
        "[1:a]aresample=48000,asetpts=PTS-STARTPTS[maina];"
        "[openinga][maina]concat=n=2:v=0:a=1[outa]"
    )
    await run_process(
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-t",
        f"{visible_seconds:.3f}",
        "-i",
        str(opening_image_path),
        "-i",
        str(video_path),
        "-f",
        "lavfi",
        "-t",
        f"{visible_seconds:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    )
    return await media_duration(output_path)


async def append_closing_image(
    video_path: Path,
    closing_image_path: Path,
    output_path: Path,
    *,
    visible_seconds: float = BRAND_CARD_SECONDS,
) -> float:
    """Append a silent full-frame brand card after the narrated presentation."""
    if not video_path.is_file() or not closing_image_path.is_file():
        raise FileNotFoundError("Video and closing image must exist before appending the end card")
    if visible_seconds <= 0:
        raise ValueError("Closing image duration must be greater than zero")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS[mainv];"
        "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
        f"trim=duration={visible_seconds:.3f},setpts=PTS-STARTPTS[endv];"
        "[mainv][endv]concat=n=2:v=1:a=0[outv];"
        "[0:a]aresample=48000,asetpts=PTS-STARTPTS[maina];"
        "[2:a]asetpts=PTS-STARTPTS[enda];"
        "[maina][enda]concat=n=2:v=0:a=1[outa]"
    )
    await run_process(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-loop",
        "1",
        "-t",
        f"{visible_seconds:.3f}",
        "-i",
        str(closing_image_path),
        "-f",
        "lavfi",
        "-t",
        f"{visible_seconds:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    )
    return await media_duration(output_path)


async def overlay_opening_logo(
    video_path: Path,
    logo_path: Path,
    output_path: Path,
    *,
    visible_seconds: float = 4,
) -> None:
    """Overlay the configured brand mark during the opening without changing audio."""
    if not video_path.is_file() or not logo_path.is_file():
        raise FileNotFoundError("Video and opening logo must exist before branding")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fade_out_start = max(visible_seconds - 0.5, 0.5)
    filter_complex = (
        "[1:v]scale=280:160:force_original_aspect_ratio=decrease,"
        "format=rgba,"
        "fade=t=in:st=0:d=0.4:alpha=1,"
        f"fade=t=out:st={fade_out_start:.2f}:d=0.5:alpha=1[brand];"
        "[0:v][brand]overlay=W-w-60:60:"
        f"enable='between(t,0,{visible_seconds:.2f})'[outv]"
    )
    await run_process(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-loop",
        "1",
        "-i",
        str(logo_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    )


async def overlay_video_watermark(
    video_path: Path,
    logo_path: Path,
    output_path: Path,
    *,
    position: str = "bottom_right",
    opacity: float = 0.35,
    width_percent: int = 10,
) -> None:
    """Apply the configured logo watermark to every frame of the assembled video."""
    if not video_path.is_file() or not logo_path.is_file():
        raise FileNotFoundError("Video and watermark logo must exist before branding")
    positions = {
        "top_left": "48:48",
        "top_right": "W-w-48:48",
        "bottom_left": "48:H-h-48",
        "bottom_right": "W-w-48:H-h-48",
    }
    if position not in positions:
        raise ValueError(f"Unsupported watermark position: {position}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_width = round(1920 * width_percent / 100)
    filter_complex = (
        f"[1:v]scale={target_width}:-1:force_original_aspect_ratio=decrease,"
        f"format=rgba,colorchannelmixer=aa={opacity:.3f}[watermark];"
        f"[0:v][watermark]overlay={positions[position]}[outv]"
    )
    await run_process(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-loop",
        "1",
        "-i",
        str(logo_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    )


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
        visual_image: VisualArtifact | None = None,
        visual_plan: VisualScenePlan | None = None,
    ) -> SceneArtifact:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        target_duration = max(audio.duration_seconds, 0.1)
        if presenter_video is None and visual_plan is not None and visual_plan.visual_beats:
            return await self._render_visual_beats(
                scene_number=scene_number,
                source_slide=source_slide,
                audio=audio,
                output_path=output_path,
                visual=visual,
                visual_image=visual_image,
                visual_plan=visual_plan,
            )
        transition_duration = min(0.28, target_duration / 4)
        fade_out_start = max(target_duration - transition_duration, 0)
        base_filter = (
            f"scale={self._width}:{self._height}:force_original_aspect_ratio=decrease,"
            f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2,"
            f"fade=t=in:st=0:d={transition_duration:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={transition_duration:.3f},format=yuv420p"
        )

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

    async def _render_visual_beats(
        self,
        *,
        scene_number: int,
        source_slide: SlideContent,
        audio: AudioArtifact,
        output_path: Path,
        visual: VisualArtifact | None,
        visual_image: VisualArtifact | None,
        visual_plan: VisualScenePlan,
    ) -> SceneArtifact:
        target_duration = max(audio.duration_seconds, 0.1)
        beats = list(visual_plan.visual_beats)
        planned_duration = sum(beat.duration_seconds for beat in beats)
        if planned_duration < target_duration:
            beats[-1] = beats[-1].model_copy(
                update={
                    "duration_seconds": beats[-1].duration_seconds
                    + target_duration
                    - planned_duration
                }
            )

        input_args: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        for index, beat in enumerate(beats):
            duration = beat.duration_seconds
            if (
                beat.kind == VisualBeatKind.GENERATED_VIDEO
                and visual is not None
                and visual.kind == "video"
            ):
                path = visual.path
                input_args.extend(["-i", str(path)])
                filters.append(
                    f"[{index}:v]scale={self._width}:{self._height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2,"
                    f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
                    f"fps={self._fps},setsar=1,settb=AVTB,format=yuv420p[v{index}]"
                )
            else:
                use_generated_image = beat.kind in {
                    VisualBeatKind.GENERATED_IMAGE,
                    VisualBeatKind.MOTION_GRAPHIC,
                }
                image_path = (
                    visual_image.path
                    if use_generated_image and visual_image is not None
                    else source_slide.image_path
                )
                input_args.extend(
                    [
                        "-loop",
                        "1",
                        "-framerate",
                        str(self._fps),
                        "-i",
                        str(image_path),
                    ]
                )
                if beat.motion_preset.value == "none":
                    image_filter = (
                        f"scale={self._width}:{self._height}:"
                        "force_original_aspect_ratio=decrease,"
                        f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2"
                    )
                else:
                    frames = max(round(duration * self._fps), 1)
                    motion = _local_motion_filter(beat.motion_preset.value, frames)
                    image_filter = (
                        "scale=2048:1152:force_original_aspect_ratio=increase,"
                        f"crop=2048:1152,{motion}:d=1:s={self._width}x{self._height}:"
                        f"fps={self._fps}"
                    )
                filters.append(
                    f"[{index}:v]{image_filter},trim=duration={duration:.3f},"
                    f"setpts=PTS-STARTPTS,fps={self._fps},setsar=1,settb=AVTB,"
                    f"format=yuv420p[v{index}]"
                )
            labels.append(f"[v{index}]")

        audio_index = len(beats)
        input_args.extend(["-i", str(audio.path)])
        transition_duration = min(0.28, target_duration / 4)
        fade_out_start = max(target_duration - transition_duration, 0)
        filters.append(
            "".join(labels)
            + f"concat=n={len(beats)}:v=1:a=0,"
            + f"fade=t=in:st=0:d={transition_duration:.3f},"
            + f"fade=t=out:st={fade_out_start:.3f}:d={transition_duration:.3f}[outv]"
        )
        await run_process(
            "ffmpeg",
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-map",
            f"{audio_index}:a:0",
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
        logger.info(
            "scene visual beat timeline rendered scene=%s beats=%s duration_seconds=%.3f",
            scene_number,
            len(beats),
            target_duration,
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
