from __future__ import annotations

import json
from pathlib import Path

from presentation_video.domain.models import BrandAssetKind, BrandKit


class FileBrandKitRepository:
    """Persists global presentation identity while keeping media in file storage."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._assets = root / "assets"
        self._config = root / "brand-kit.json"
        self._assets.mkdir(parents=True, exist_ok=True)

    def get(self) -> BrandKit:
        if not self._config.is_file():
            kit = BrandKit()
            self._write(kit)
            return kit
        return BrandKit.model_validate_json(self._config.read_text(encoding="utf-8"))

    def update(self, requested: BrandKit) -> BrandKit:
        current = self.get()
        updated = requested.model_copy(
            update={
                "version": current.version + 1,
                "logo_path": current.logo_path,
                "opening_image_path": current.opening_image_path,
                "closing_image_path": current.closing_image_path,
            }
        )
        self._write(updated)
        return updated

    def save_asset(
        self,
        kind: BrandAssetKind,
        content: bytes,
        suffix: str,
    ) -> BrandKit:
        current = self.get()
        next_version = current.version + 1
        destination = self._assets / f"{kind.value}-v{next_version}{suffix}"
        destination.write_bytes(content)
        field = {
            BrandAssetKind.LOGO: "logo_path",
            BrandAssetKind.OPENING_IMAGE: "opening_image_path",
            BrandAssetKind.CLOSING_IMAGE: "closing_image_path",
        }[kind]
        updated = current.model_copy(
            update={"version": next_version, field: destination}
        )
        self._write(updated)
        return updated

    def asset_path(self, kind: BrandAssetKind) -> Path | None:
        kit = self.get()
        return {
            BrandAssetKind.LOGO: kit.logo_path,
            BrandAssetKind.OPENING_IMAGE: kit.opening_image_path,
            BrandAssetKind.CLOSING_IMAGE: kit.closing_image_path,
        }[kind]

    def _write(self, kit: BrandKit) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = self._config.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(kit.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self._config)
