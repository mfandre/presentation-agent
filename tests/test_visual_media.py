from pathlib import Path

from presentation_video.domain.models import MediaMode, SlideContent, VisualScenePlan
from presentation_video.infrastructure.visual_media import _visual_prompt


def test_visual_prompt_is_didactic_and_grounded_in_slide_content() -> None:
    plan = VisualScenePlan(
        scene_number=1,
        source_slide_numbers=[1],
        prompt="Show how governed data moves from ingestion to analytics",
        media_mode=MediaMode.VIDEO,
    )
    slide = SlideContent(
        number=1,
        title="Governança de dados",
        body_text="Catálogo central, políticas de acesso e linhagem ponta a ponta.",
        image_path=Path("slide.png"),
    )

    prompt = _visual_prompt(plan, [slide])

    assert "grounded, plausible real-world image" in prompt
    assert "Governança de dados" in prompt
    assert "Catálogo central" in prompt
    assert "Never create an isometric view, 3D diorama, miniature" in prompt
    assert "show no words, letters, numbers" in prompt
    assert "monitors, screens, UI, dashboards" in prompt
