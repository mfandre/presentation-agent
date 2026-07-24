from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from presentation_video.bootstrap import build_pipeline
from presentation_video.domain.models import VideoJobRequest
from presentation_video.settings import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a narrated video from PPTX or PDF")
    parser.add_argument("source", type=Path)
    parser.add_argument("--duration", type=int, default=None, help="Target duration in seconds")
    parser.add_argument("--language", default="pt-BR")
    parser.add_argument("--audience", default="executive")
    parser.add_argument("--tone", default="professional and natural")
    return parser.parse_args()


async def _run() -> None:
    args = parse_args()
    settings = Settings()
    pipeline = build_pipeline(settings)
    result = await pipeline.execute(
        VideoJobRequest(
            source_path=args.source.resolve(),
            target_seconds=args.duration or settings.target_seconds,
            language=args.language,
            audience=args.audience,
            tone=args.tone,
        )
    )
    print(result.video_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
