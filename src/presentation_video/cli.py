from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from presentation_video.bootstrap import build_pipeline
from presentation_video.domain.models import ProductionMode, VideoJobRequest
from presentation_video.settings import Settings
from presentation_video.workflow.loader import WorkflowLoader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a narrated video from PPTX or PDF")
    parser.add_argument("source", type=Path)
    parser.add_argument("--duration", type=int, default=None, help="Target duration in seconds")
    parser.add_argument("--language", default="pt-BR")
    parser.add_argument("--audience", default="executive")
    parser.add_argument("--tone", default="professional and natural")
    parser.add_argument(
        "--production-mode",
        choices=[mode.value for mode in ProductionMode],
        default=ProductionMode.HYBRID_PRESENTATION.value,
    )
    return parser.parse_args()


async def _run() -> None:
    args = parse_args()
    settings = Settings()
    workflow = WorkflowLoader(settings.workflow_root).load(settings.default_workflow)
    pipeline = build_pipeline(settings, workflow=workflow)
    target_seconds = int(workflow.inputs["target_seconds"].default or 600)
    result = await pipeline.execute(
        VideoJobRequest(
            source_path=args.source.resolve(),
            target_seconds=args.duration or target_seconds,
            language=args.language,
            audience=args.audience,
            tone=args.tone,
            production_mode=ProductionMode(args.production_mode),
        )
    )
    print(result.video_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
