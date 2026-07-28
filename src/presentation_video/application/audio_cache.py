from __future__ import annotations

import wave
from pathlib import Path


def valid_wav_duration(path: Path) -> float | None:
    """Return the duration only when a cached WAV is complete and readable."""

    if not path.is_file() or path.stat().st_size <= 44:
        return None
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            duration = audio.getnframes() / frame_rate if frame_rate else 0
    except (OSError, EOFError, wave.Error):
        return None
    return duration if duration > 0 else None
