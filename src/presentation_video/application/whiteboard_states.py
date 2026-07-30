from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from presentation_video.domain.models import VisualArtifact


def _state_stem(scene_number: int, state_number: int, revision: int) -> str:
    return (
        f"scene-{scene_number:03d}-whiteboard-state-{state_number:03d}"
        f"-r{revision}.png"
    )


def _progressive_mask(size: tuple[int, int], progress: float) -> Image.Image:
    """Build a slightly irregular left-to-right marker reveal mask."""

    width, height = size
    if progress <= 0:
        return Image.new("L", size, 0)
    if progress >= 1:
        return Image.new("L", size, 255)
    amplitude = max(12, round(width * 0.012))
    frontier = progress * (width + amplitude * 2) - amplitude
    points = [(0, 0)]
    step = max(8, height // 80)
    for y in range(0, height + step, step):
        wobble = math.sin((y / max(height, 1)) * math.tau * 3.0) * amplitude
        points.append((round(frontier + wobble), min(y, height)))
    points.extend([(0, height), (0, 0)])
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def build_progressive_whiteboard_states(
    master: VisualArtifact,
    state_count: int,
    output_dir: Path,
) -> list[VisualArtifact]:
    """Derive cumulative review frames from one locked final whiteboard illustration."""

    if state_count < 1:
        raise ValueError("whiteboard requires at least one progressive state")
    if not master.path.is_file() or master.path.stat().st_size == 0:
        raise FileNotFoundError(f"whiteboard master image is missing: {master.path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(master.path) as source:
        final_image = source.convert("RGB")
    blank = Image.new("RGB", final_image.size, "white")
    states: list[VisualArtifact] = []
    for index in range(0, state_count + 1):
        progress = index / state_count
        state_path = output_dir / _state_stem(
            master.scene_number,
            index,
            master.revision,
        )
        frame = (
            blank.copy()
            if index == 0
            else final_image.copy()
            if index == state_count
            else Image.composite(
                final_image,
                blank,
                _progressive_mask(final_image.size, progress),
            )
        )
        frame.save(state_path, format="PNG", optimize=True)
        if index > 0:
            states.append(
                VisualArtifact(
                    scene_number=master.scene_number,
                    shot_number=index,
                    path=state_path,
                    start_path=output_dir
                    / _state_stem(
                        master.scene_number,
                        index - 1,
                        master.revision,
                    ),
                    kind="image",
                    revision=master.revision,
                )
            )
    return states


def previous_whiteboard_state(image: VisualArtifact) -> Path:
    if image.shot_number < 1:
        raise ValueError("whiteboard shot number must be positive")
    return image.path.with_name(
        _state_stem(
            image.scene_number,
            image.shot_number - 1,
            image.revision,
        )
    )
