#!/usr/bin/env python3
"""Run one isolated Gemini Omni image-to-video generation.

Example:
    .venv/bin/python scripts/test_gemini_omni.py \
      --image work/<job>/images/scene-001-r1.png \
      --gcs-output gs://my-bucket/text-2-video-tests
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from presentation_video.domain.models import (
    MediaMode,
    MotionPreset,
    VisualArtifact,
    VisualScenePlan,
)
from presentation_video.infrastructure.gemini_omni import (
    GeminiOmniVideoAssetGenerator,
)
from presentation_video.settings import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one short video from an image using Gemini Omni on Vertex AI.",
    )
    parser.add_argument("--image", type=Path, required=True, help="Input PNG, JPEG, or WebP.")
    parser.add_argument(
        "--prompt",
        default="Subtle natural movement in the scene while preserving the approved composition.",
        help="Observable action to animate. Text and new objects remain forbidden.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("work/omni-test"))
    parser.add_argument("--project", help="GCP project; defaults to GOOGLE_CLOUD_PROJECT.")
    parser.add_argument(
        "--gcs-output",
        help="GCS staging prefix for the input image; defaults to VERTEX_VIDEO_OUTPUT_GCS_URI.",
    )
    parser.add_argument(
        "--store-output-in-gcs",
        action="store_true",
        help="Store the generated MP4 in GCS instead of receiving it inline.",
    )
    parser.add_argument("--model", default="gemini-omni-flash-preview")
    parser.add_argument("--aspect-ratio", choices=("16:9", "9:16"), default="16:9")
    parser.add_argument("--duration", type=int, choices=range(3, 11), default=8)
    parser.add_argument("--language", default="pt-BR")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> Path:
    settings = Settings()
    image_path = args.image.expanduser().resolve()
    if not image_path.is_file() or image_path.stat().st_size == 0:
        raise SystemExit(f"Input image does not exist or is empty: {image_path}")
    if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise SystemExit("Input image must be PNG, JPEG, or WebP.")

    project = (args.project or settings.google_cloud_project or "").strip()
    gcs_output = (
        args.gcs_output or settings.vertex_video_output_gcs_uri or ""
    ).strip()
    if not project:
        raise SystemExit(
            "Missing GCP project. Pass --project or configure GOOGLE_CLOUD_PROJECT."
        )
    if not gcs_output:
        raise SystemExit(
            "Missing GCS output prefix. Pass --gcs-output gs://bucket/prefix or configure "
            "VERTEX_VIDEO_OUTPUT_GCS_URI."
        )

    output_dir = args.output_dir.expanduser().resolve()
    plan = VisualScenePlan(
        scene_number=1,
        shot_number=1,
        content_language=args.language,
        prompt=args.prompt,
        media_mode=MediaMode.VIDEO,
        preserve_source_frame=False,
        camera_motion="locked framing with subtle natural parallax",
        motion_preset=MotionPreset.SLOW_PUSH,
        entrance_motion="begin exactly from the supplied image",
        focal_action=args.prompt,
        transition_out="finish on a stable frame that preserves the original composition",
        visual_style="grounded corporate documentary",
    )
    image = VisualArtifact(
        scene_number=1,
        shot_number=1,
        path=image_path,
        kind="image",
    )
    generator = GeminiOmniVideoAssetGenerator(
        project=project,
        output_gcs_uri=gcs_output,
        model=args.model,
        aspect_ratio=args.aspect_ratio,
        clip_duration_seconds=args.duration,
        store_output_in_gcs=args.store_output_in_gcs,
    )
    print(
        f"Submitting Gemini Omni generation: model={args.model} project={project} "
        f"duration={args.duration}s image={image_path}"
    )
    artifact = await generator.animate(
        plan,
        image,
        output_dir,
        duration_seconds=float(args.duration),
    )
    print(f"Video generated: {artifact.path} ({artifact.path.stat().st_size} bytes)")
    return artifact.path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
