from __future__ import annotations

from pathlib import Path

from presentation_video.domain.models import AudioArtifact
from presentation_video.domain.ports import AvatarRenderer


class NoAvatarRenderer(AvatarRenderer):
    async def render(
        self,
        avatar_reference: Path | None,
        audio: AudioArtifact,
        output_path: Path,
    ) -> Path | None:
        return None


class AvatarSidecarRenderer(AvatarRenderer):
    """
    Contract for a separately deployed GPU service such as MuseTalk or SadTalker.

    Keep GPU-specific dependencies outside the API/orchestration service. Implement this adapter
    with your preferred HTTP client and your own sidecar contract. The main pipeline remains unchanged.
    """

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    async def render(
        self,
        avatar_reference: Path | None,
        audio: AudioArtifact,
        output_path: Path,
    ) -> Path | None:
        if avatar_reference is None:
            return None
        raise NotImplementedError(
            f"Implement the sidecar call to {self._endpoint} using your own upload/storage contract."
        )
