from __future__ import annotations

import wave
from pathlib import Path

import pytest

from presentation_video.application.dialogue import (
    build_character_voice_map,
    synthesize_scene_audio,
)
from presentation_video.application.production_presets import (
    direct_narrative_tone,
    get_production_preset,
    transform_visual_plan,
)
from presentation_video.domain.models import (
    AudioArtifact,
    CharacterProfile,
    CreativeDirection,
    DialogueLine,
    MediaMode,
    PresentationScript,
    PresentationVisualPlan,
    ProductionMode,
    SceneScript,
    VisualScenePlan,
)


class FakeVoiceSynthesizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
        voice: str | None = None,
    ) -> AudioArtifact:
        self.calls.append((text, voice, style))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8_000)
            audio.writeframes(b"\x00\x00" * 800)
        return AudioArtifact(path=output_path, duration_seconds=0.1)


def test_cinematic_preset_exposes_narration_and_character_dialogue() -> None:
    preset = get_production_preset(ProductionMode.CINEMATIC_STORY)
    option = next(item for item in preset.options if item.id == "speech_mode")

    assert option.default == "narration"
    assert [(choice.value, choice.label) for choice in option.choices] == [
        ("narration", "Narração"),
        ("character_dialogue", "Diálogo entre personagens"),
    ]
    assert "SPEECH MODE" not in direct_narrative_tone(
        ProductionMode.CINEMATIC_STORY,
        "natural",
        {"speech_mode": "narration"},
    )
    dialogue_direction = direct_narrative_tone(
        ProductionMode.CINEMATIC_STORY,
        "natural",
        {"speech_mode": "character_dialogue"},
    )
    assert "SPEECH MODE: CHARACTER DIALOGUE" in dialogue_direction
    assert "not through an omniscient narrator" in dialogue_direction


@pytest.mark.asyncio
async def test_dialogue_audio_uses_stable_character_voices_and_joins_lines(
    tmp_path: Path,
) -> None:
    scene = SceneScript(
        scene_number=1,
        source_slide_numbers=[1],
        narration="Vamos agora. Eu vou com você. Então está combinado.",
        target_seconds=12,
        dialogue=[
            DialogueLine(
                character_id="ana",
                character_name="Ana",
                text="Vamos agora.",
                emotion="decidida",
            ),
            DialogueLine(
                character_id="bruno",
                character_name="Bruno",
                text="Eu vou com você.",
                emotion="solidário",
            ),
            DialogueLine(
                character_id="ana",
                character_name="Ana",
                text="Então está combinado.",
                emotion="aliviada",
            ),
        ],
    )
    synthesizer = FakeVoiceSynthesizer()
    script = PresentationScript(title="Conversa", scenes=[scene], total_estimated_seconds=12)
    voice_map = build_character_voice_map(script, ("Kore", "Puck", "Aoede"))

    artifact = await synthesize_scene_audio(
        scene,
        tmp_path / "scene-001.wav",
        synthesizer,
        language="pt-BR",
        style="natural",
        dialogue_mode=True,
        voices=("Kore", "Puck", "Aoede"),
        voice_map=voice_map,
    )

    assert artifact.path.is_file()
    assert artifact.duration_seconds == pytest.approx(0.66)
    assert synthesizer.calls[0][1] == synthesizer.calls[2][1]
    assert synthesizer.calls[0][1] != synthesizer.calls[1][1]
    assert "Ana" in str(synthesizer.calls[0][2])
    assert "decidida" in str(synthesizer.calls[0][2])


def test_dialogue_mode_directs_visible_speaker_and_listener_performance() -> None:
    scene = SceneScript(
        scene_number=1,
        source_slide_numbers=[1],
        narration="Você ouviu isso? Ouvi, veio da floresta.",
        target_seconds=8,
        media_mode=MediaMode.VIDEO,
        dialogue=[
            DialogueLine(
                character_id="ana",
                character_name="Ana",
                text="Você ouviu isso?",
                emotion="surprised",
            ),
            DialogueLine(
                character_id="bruno",
                character_name="Bruno",
                text="Ouvi, veio da floresta.",
                emotion="alert",
            ),
        ],
    )
    script = PresentationScript(
        title="Conversa",
        creative_direction=CreativeDirection(
            characters=[
                CharacterProfile(
                    id="ana",
                    narrative_role="protagonist",
                    physical_appearance="adult woman with dark curly hair",
                    wardrobe="terracotta jacket",
                ),
                CharacterProfile(
                    id="bruno",
                    narrative_role="companion",
                    physical_appearance="adult man with short black hair",
                    wardrobe="charcoal overshirt",
                ),
            ]
        ),
        scenes=[scene],
        total_estimated_seconds=8,
    )
    plan = PresentationVisualPlan(
        creative_direction=script.creative_direction,
        scenes=[
            VisualScenePlan(
                scene_number=1,
                prompt="Ana and Bruno stop on a forest trail.",
                media_mode=MediaMode.VIDEO,
                preserve_source_frame=False,
                recurring_character_ids=["ana", "bruno"],
            )
        ],
    )

    directed = transform_visual_plan(
        ProductionMode.CINEMATIC_STORY,
        plan,
        {"speech_mode": "character_dialogue"},
        script,
    )

    assert "DIALOGUE PERFORMANCE:" in directed.scenes[0].prompt
    assert "Ana (surprised): Você ouviu isso?" in directed.scenes[0].prompt
    assert "speaker and listener reactions" in directed.scenes[0].focal_action
