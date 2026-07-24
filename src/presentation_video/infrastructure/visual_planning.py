from __future__ import annotations

import json
import logging

from pydantic_ai import Agent

from presentation_video.domain.models import (
    MediaMode,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    VisualScenePlan,
)
from presentation_video.infrastructure.replicate import ReplicatePredictionClient

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """
You are a technical documentary director. Turn each narrative scene into a grounded visual that
could plausibly be photographed in a real workplace or published in technical documentation.

Grounding rules:
- Base every visible element on the cited source pages, their notes, or the scene narration.
- First identify the concrete evidence in the source: an actual screen, table, chart, system
  component, workflow step, physical environment, named tool, role, or observable action.
- Prefer, in this order: a faithful operational screen or artifact; a realistic close-up of a
  specific person performing the described task with real tools; or a clean flat 2D technical
  diagram using only source-grounded components and connections.
- Treat monitors, dashboards, tables, access matrices, catalog records, lineage graphs, code,
  terminals, and architecture diagrams as reasons to use a static source page. Never recreate
  them inside a generative video scene.
- If the source is strategic or abstract, depict a concrete artifact being reviewed or a specific
  action being performed. Do not translate the concept into a physical-world metaphor.
- Avoid generic corporate meetings, anonymous business people, handshakes, decorative abstraction,
  glowing networks, floating icons, vague futuristic cities, and stock-photo compositions.
- Never use isometric or 3D miniature scenes, dioramas, clay renders, toy models, model cities,
  symbolic factories, physical pipes for data pipelines, gates or padlocks for access control,
  bridges for integration, conveyor belts, shields, icon grids, or glossy infographics unless the
  source literally depicts those physical objects.
- Preserve source facts and never depict unsupported metrics, brands, people, or conclusions.

Hybrid editing rules:
- media_mode is chosen by the narrative script and must be preserved exactly.
- For media_mode="static", choose source_slide_number from that scene's source_slide_numbers. Pick
  the single page that best expresses the beat. It will be shown unchanged, not regenerated or
  sent to image-to-video. Set camera_motion to "none" and use prompt to explain why it is the best
  readable anchor.
- For media_mode="video", source_slide_number must be null. Write a prompt for a short, natural,
  word-free moving shot. Absolutely no words, letters, numbers, captions, logos, signs, documents,
  presentation pages, monitors, dashboards, terminals, interfaces, charts, or tables may appear.
- Write every media_mode="video" prompt and its camera_motion in English because the downstream
  image-to-video model accepts English prompts. Source facts can remain in their original language,
  but translate their visual interpretation without changing their meaning.
- Do not alternate formats mechanically. Follow the story beat and transition supplied by the
  script, maintaining visual continuity of palette, setting, subjects, direction of movement, and
  emotional energy across static and video scenes.

Continuity rules:
- Keep recurring people, locations, palette, lighting, era, and art direction coherent.
- The still-image prompt must describe visible subjects, their relationships, setting,
  composition, lighting, and concrete action. Camera movement belongs in camera_motion.
- Readable interfaces and technical artifacts appear only in unchanged static source pages.
- Produce exactly one visual plan for every narrative scene, in narrative order. The number of
  scenes is independent from the number of source pages.
""".strip()


class DebugVisualPlanner:
    """Deterministic, network-free visual planning for end-to-end development."""

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        source = {slide.number: slide for slide in document.slides}
        scenes: list[VisualScenePlan] = []
        for item in script.scenes:
            pages = [source[number] for number in item.source_slide_numbers]
            evidence = " ".join(
                part
                for page in pages
                for part in (page.title.strip(), page.body_text.strip())
                if part
            )
            evidence = " ".join(evidence.split())[:1_200]
            is_static = item.media_mode == MediaMode.STATIC
            scenes.append(
                VisualScenePlan(
                    scene_number=item.scene_number,
                    source_slide_numbers=item.source_slide_numbers,
                    media_mode=item.media_mode,
                    source_slide_number=item.source_slide_numbers[0] if is_static else None,
                    story_beat=item.story_beat,
                    prompt=(
                        (
                            "Use this unchanged source page as a crisp readable information anchor. "
                            if is_static
                            else "Show a concrete, natural real-world action with absolutely no "
                            "text, letters, numbers, screens, documents, signs, or logos. "
                        )
                        + f"Story intent: {item.visual_intent}. "
                        + f"Source evidence: {evidence or item.narration}"
                    ),
                    camera_motion=(
                        "none" if is_static else "natural camera movement at normal speed"
                    ),
                )
            )
        logger.info("visual plan created provider=debug scenes=%s", len(scenes))
        return PresentationVisualPlan(scenes=scenes)


def _payload(document: PresentationDocument, script: PresentationScript) -> dict[str, object]:
    source = {slide.number: slide for slide in document.slides}
    return {
        "presentation_title": document.title,
        "scenes": [
            {
                "scene_number": item.scene_number,
                "source_slide_numbers": item.source_slide_numbers,
                "media_mode": item.media_mode,
                "story_beat": item.story_beat,
                "visual_intent": item.visual_intent,
                "transition_to_next": item.transition_to_next,
                "short_caption": item.short_caption,
                "source_pages": [
                    {
                        "slide_number": number,
                        "title": source[number].title,
                        "source_text": source[number].body_text,
                        "speaker_notes": source[number].speaker_notes,
                    }
                    for number in item.source_slide_numbers
                ],
                "narration": item.narration,
                "duration_seconds": item.target_seconds,
            }
            for item in script.scenes
        ],
    }


def _validate_sequence(plan: PresentationVisualPlan, script: PresentationScript) -> None:
    expected = [item.scene_number for item in script.scenes]
    actual = [item.scene_number for item in plan.scenes]
    if actual != expected:
        raise ValueError(f"Visual planner returned scene sequence {actual}; expected {expected}")
    scripts_by_scene = {item.scene_number: item for item in script.scenes}
    for scene in plan.scenes:
        script_scene = scripts_by_scene[scene.scene_number]
        scene.source_slide_numbers = script_scene.source_slide_numbers
        scene.media_mode = script_scene.media_mode
        scene.story_beat = script_scene.story_beat
        if scene.media_mode == MediaMode.STATIC:
            if scene.source_slide_number not in scene.source_slide_numbers:
                scene.source_slide_number = scene.source_slide_numbers[0]
            scene.camera_motion = "none"
        else:
            scene.source_slide_number = None
            hard_exclusions = (
                "text, words, letters, numbers, typography, subtitles, captions, logos, signs, "
                "documents, slides, monitors, screens, UI, dashboards, charts, tables"
            )
            if hard_exclusions not in scene.negative_prompt:
                scene.negative_prompt = f"{hard_exclusions}, {scene.negative_prompt}"


class PydanticAIVisualPlanner:
    def __init__(self, model: str) -> None:
        self._agent = Agent(
            model,
            output_type=PresentationVisualPlan,
            system_prompt=_SYSTEM_PROMPT,
        )

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        result = await self._agent.run(json.dumps(_payload(document, script), ensure_ascii=False))
        plan = result.output
        _validate_sequence(plan, script)
        return plan


class ReplicateVisualPlanner:
    """Runs a JSON-capable LLM hosted by Replicate and validates its output with Pydantic."""

    def __init__(
        self,
        client: ReplicatePredictionClient,
        model: str,
        input_defaults: dict[str, object] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._input_defaults = input_defaults or {}

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        schema = json.dumps(PresentationVisualPlan.model_json_schema(), ensure_ascii=False)
        prompt = (
            f"{_SYSTEM_PROMPT}\n\nReturn JSON only, matching this schema:\n{schema}\n\nInput:\n"
            f"{json.dumps(_payload(document, script), ensure_ascii=False)}"
        )
        output = await self._client.run(
            self._model,
            {**self._input_defaults, "prompt": prompt},
        )
        text = self._client.output_text(output)
        plan = PresentationVisualPlan.model_validate_json(self._strip_fence(text))
        _validate_sequence(plan, script)
        return plan

    @staticmethod
    def _strip_fence(value: str) -> str:
        value = value.strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[-1]
            value = value.rsplit("```", 1)[0]
        return value.strip()
