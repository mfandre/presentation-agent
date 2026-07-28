from pathlib import Path

from presentation_video.application.captions import (
    build_caption_cues,
    write_caption_files,
)
from presentation_video.domain.models import (
    MediaMode,
    PresentationScript,
    SceneArtifact,
    SceneScript,
)


def test_caption_files_cover_rendered_timeline_and_export_vtt_srt(tmp_path: Path) -> None:
    script = PresentationScript(
        title="Training",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="Primeiro conceito explicado. Depois vem um exemplo objetivo.",
                target_seconds=6,
                media_mode=MediaMode.VIDEO,
            ),
            SceneScript(
                scene_number=2,
                source_slide_numbers=[2],
                narration="Por fim, revise o aprendizado.",
                target_seconds=4,
                media_mode=MediaMode.STATIC,
            ),
        ],
        total_estimated_seconds=10,
    )
    rendered = [
        SceneArtifact(scene_number=1, path=tmp_path / "one.mp4", duration_seconds=6),
        SceneArtifact(scene_number=2, path=tmp_path / "two.mp4", duration_seconds=4),
    ]

    cues = build_caption_cues(script, rendered)
    vtt_path, srt_path = write_caption_files(cues, tmp_path, "pt-BR")

    assert cues[0].start_seconds == 0
    assert cues[-1].end_seconds == 10
    assert vtt_path.name == "captions.pt-BR.vtt"
    assert srt_path.name == "captions.pt-BR.srt"
    assert vtt_path.read_text(encoding="utf-8").startswith("WEBVTT\n")
    assert "00:00:00.000 -->" in vtt_path.read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in srt_path.read_text(encoding="utf-8")
