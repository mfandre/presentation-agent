from pathlib import Path

from PIL import Image, ImageDraw

from presentation_video.application.whiteboard_states import (
    build_progressive_whiteboard_states,
)
from presentation_video.domain.models import VisualArtifact


def test_progressive_whiteboard_states_share_one_locked_master(
    tmp_path: Path,
) -> None:
    master_path = tmp_path / "master.png"
    master = Image.new("RGB", (600, 300), "white")
    drawing = ImageDraw.Draw(master)
    drawing.rectangle((40, 60, 150, 180), outline="black", width=8)
    drawing.ellipse((245, 60, 355, 180), outline="black", width=8)
    drawing.polygon([(460, 180), (520, 60), (580, 180)], outline="black")
    master.save(master_path)

    states = build_progressive_whiteboard_states(
        VisualArtifact(scene_number=2, path=master_path, kind="image"),
        3,
        tmp_path / "states",
    )

    assert [state.shot_number for state in states] == [1, 2, 3]
    assert all(state.start_path is not None and state.start_path.is_file() for state in states)
    with Image.open(states[0].start_path) as blank:
        assert blank.convert("RGB").getbbox() == (0, 0, 600, 300)
        assert blank.getpixel((50, 70)) == (255, 255, 255)
    with Image.open(states[-1].path) as final_state:
        with Image.open(master_path) as expected:
            assert list(final_state.getdata()) == list(expected.getdata())
    assert states[0].path.read_bytes() != states[1].path.read_bytes()
    assert states[1].path.read_bytes() != states[2].path.read_bytes()
