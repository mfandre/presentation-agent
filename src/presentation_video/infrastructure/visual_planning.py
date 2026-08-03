from __future__ import annotations

import json
import logging
import math

from pydantic import ValidationError
from pydantic_ai import Agent

from presentation_video.domain.models import (
    MediaMode,
    MotionPreset,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    VisualScenePlan,
    TransitionPreset,
    VisualBeat,
    VisualBeatKind,
    build_default_visual_beats,
)
from presentation_video.infrastructure.replicate import ReplicatePredictionClient
from presentation_video.infrastructure.concept_grounding import (
    default_concept_visualization,
    infer_required_concepts,
)

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
- Prefer an unchanged static source page for readable monitors, dashboards, tables, code, and
  architecture diagrams. A video may use a clean, non-readable software-process visualization
  when AI, agents, orchestration, data flow, or governance is itself a central source concept.
- If the source is strategic or abstract, depict a concrete artifact being reviewed or a specific
  action being performed. Do not translate the concept into a physical-world metaphor.
- Avoid generic corporate meetings, anonymous business people, handshakes, decorative abstraction,
  glowing networks, floating icons, vague futuristic cities, and stock-photo compositions.
- Never use isometric or 3D miniature scenes, dioramas, clay renders, toy models, model cities,
  symbolic factories, physical pipes for data pipelines, gates or padlocks for access control,
  bridges for integration, conveyor belts, shields, icon grids, or glossy infographics unless the
  source literally depicts those physical objects.
- Preserve source facts and never depict unsupported metrics, brands, people, or conclusions.
- A video scene is not a summary montage. Select exactly ONE source-grounded visual anchor, ONE
  location, and ONE observable action. Do not attempt to visualize every idea in the narration.
- Prefer close views of hands, tools, materials, equipment, or routine work over generic groups,
  leaders walking, meetings, symbolic gestures, or anonymous people posing for the camera.
- Never invent an industrial setting merely because the company works in energy. Use a plant,
  laboratory, office, field site, control room, or geological material only when the cited pages
  explicitly support that setting or object.

Hybrid editing rules:
- media_mode is chosen by the narrative script and must be preserved exactly.
- For media_mode="static", choose source_slide_number from that scene's source_slide_numbers. Pick
  the single page that best expresses the beat only when source_frame_suitable=true. Set
  preserve_source_frame=true and show it unchanged. When all cited pages are text-heavy editorial
  pages with source_frame_suitable=false, set preserve_source_frame=false,
  source_slide_number=null, and design a source-grounded editorial illustration from the
  storytelling instead of displaying paragraphs from the document.
- For media_mode="video", source_slide_number must be null. Write a prompt for a short, natural,
  word-free moving shot. No readable words, letters, numbers, captions, logos, signs, documents,
  or presentation pages may appear. Non-readable software interfaces are allowed only when they
  make a source-grounded AI, agent, data-flow, or governance concept visible.
- For every video scene, populate action_progression with exactly ceil(target_seconds / 8)
  chronological, mutually exclusive visual actions. Each item is the single physical state change
  assigned to one downstream take. Start with what happens first and end with the visible outcome.
  Never repeat an action, reveal a later outcome early, or summarize the whole scene in each item.
  Example: ["boat enters the water, fish not yet caught", "line tightens and fish is lifted,
  fish not yet released", "hands release fish and it swims away"].
- Populate must_show_concepts with the central source concepts that would make the scene misleading
  if omitted. Populate concept_visualization with a concrete account of how each will be visible.
- Copy every critical_information item supplied by the narrative scene. An item with
  exact_display_required=true must receive an exact static information anchor; never ask an image
  or video model to rewrite its values, roles, deadlines, or table cells.
  When the narration says AI agents, do not replace them with generic people, equipment, or sensors:
  visibly show multiple software task modules, tool handoffs, orchestration, and human oversight.
- Depict AI as credible software workflow, never as a robot, humanoid, glowing brain, magic orb,
  hologram, neon network, or vague futuristic effect.
- Keep generated scenes safe and routine: anonymous adults only, faces not emphasized, correct PPE
  where relevant, no public figures, politics, protests, conflict, weapons, fire, smoke, explosions,
  injuries, emergencies, dangerous behavior, medical procedures, minors, nudity, or sexual content.
- Do not use words such as attack, threat, battle, war, weapon, explosive, disaster, victim, or
  surveillance as visual metaphors. Translate strategic tension into neutral operational work.
- Write every media_mode="video" prompt and its camera_motion in English because the downstream
  image-to-video model accepts English prompts. Source facts can remain in their original language,
  but translate their visual interpretation without changing their meaning.
- Do not alternate formats mechanically. Follow the story beat and transition supplied by the
  script, maintaining visual continuity of palette, setting, subjects, direction of movement, and
  emotional energy across static and video scenes.

Continuity rules:
- Keep recurring people, locations, palette, lighting, era, and art direction coherent.
- When a person appears in more than one scene, create exactly one stable entry in
  creative_direction.characters. Give it a short lowercase id and lock concrete, visually
  observable traits: apparent age range, face shape, skin tone, hair, facial hair, build,
  wardrobe, accessories, and distinctive identity markers. Never change these traits later.
- Set recurring_character_ids on every scene where those people appear. Reuse the same id across
  the complete story; never describe the same recurring person as a new character. Do not add a
  character profile for anonymous background people who appear only once.
- When a scene contains dialogue, stage the named speaker and listeners as those matching recurring
  characters. Preserve speaking order, emotion, eye-lines, and natural reactions; never render the
  spoken words as visible text.
- The still-image prompt must describe visible subjects, their relationships, setting,
  composition, lighting, and concrete action. Camera movement belongs in camera_motion.
- Readable interfaces and technical artifacts appear only in unchanged static source pages.
- Produce exactly one visual plan for every narrative scene, in narrative order. The number of
  scenes is independent from the number of source pages.
- Treat creative_direction as a locked style bible. Carry its palette, accent, visual motif,
  throughline, and pacing through every scene without adding unsupported imagery.
- Treat central_thesis, narrative_device, recurring_visual_principle, transformation axis, and
  concept_mappings as the presentation's narrative grammar. Do not confine them to the opening.
  Each scene must advance that grammar in a way supported by its own cited pages.
- Copy scene_purpose, relationship_to_thesis, and narrative_progress from the script. For every
  scene populate visible_evidence with concrete things the viewer will actually see and
  forbidden_substitutions with plausible but misleading generic alternatives.
- A plan is globally coherent only when its generated scenes visibly advance the central thesis.
  Local topic relevance is insufficient. A geology image, industrial worker, document stack, or
  executive meeting must be rejected when it omits the scene's relationship to the thesis.
- For every scene choose a concrete motion_preset, entrance_motion, focal_action,
  transition_out, transition_preset, and optional emphasis_beats_seconds.
- Use motion_preset="none" for static source pages. For video scenes choose slow_push, pull_back,
  pan_left, pan_right, or drift_up according to the action and transition. Alternate scale and
  direction intentionally; do not repeat the same preset mechanically.
- Break each scene into visual_beats whose durations cover the complete scene duration. A generated
  video beat may appear at most once because downstream clips are short and must never loop.
  Complete longer scenes with generated_image, source_slide, or motion_graphic beats. Preserve
  readable source slides and alternate visual scale intentionally.
""".strip()


class DebugVisualPlanner:
    """Deterministic, network-free visual planning for end-to-end development."""

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        source = {slide.number: slide for slide in document.slides}
        scenes: list[VisualScenePlan] = []
        for index, item in enumerate(script.scenes):
            pages = [source[number] for number in item.source_slide_numbers]
            evidence = " ".join(
                part
                for page in pages
                for part in (page.title.strip(), page.body_text.strip())
                if part
            )
            evidence = " ".join(evidence.split())[:1_200]
            is_static = item.media_mode == MediaMode.STATIC
            suitable_pages = [page for page in pages if page.source_frame_suitable]
            preserve_source = is_static and bool(suitable_pages)
            concepts = infer_required_concepts(f"{item.narration} {item.visual_intent} {evidence}")
            scenes.append(
                VisualScenePlan(
                    scene_number=item.scene_number,
                    source_slide_numbers=item.source_slide_numbers,
                    media_mode=item.media_mode,
                    source_slide_number=suitable_pages[0].number if preserve_source else None,
                    preserve_source_frame=preserve_source,
                    story_beat=item.story_beat,
                    must_show_concepts=concepts,
                    concept_visualization=default_concept_visualization(concepts),
                    scene_purpose=item.scene_purpose or item.visual_intent,
                    relationship_to_thesis=(
                        item.relationship_to_thesis
                        or script.creative_direction.recurring_visual_principle
                        or script.creative_direction.throughline
                    ),
                    narrative_progress=item.narrative_progress or item.story_beat,
                    visible_evidence=[
                        item.visual_intent,
                        f"Source-grounded anchor: {evidence[:300] or item.narration[:300]}",
                    ],
                    forbidden_substitutions=[
                        "generic corporate teamwork",
                        "industry imagery that does not advance the central thesis",
                    ],
                    prompt=(
                        (
                            "Use this unchanged source page as a crisp readable information anchor. "
                            if preserve_source
                            else "Show a concrete, natural real-world action with absolutely no "
                            "text, letters, numbers, screens, documents, signs, or logos. "
                        )
                        + f"Story intent: {item.visual_intent}. "
                        + f"Source evidence: {evidence or item.narration}"
                    ),
                    camera_motion=(
                        "none" if preserve_source else "natural camera movement at normal speed"
                    ),
                    motion_preset=(
                        MotionPreset.NONE
                        if preserve_source
                        else [
                            MotionPreset.SLOW_PUSH,
                            MotionPreset.PAN_RIGHT,
                            MotionPreset.PULL_BACK,
                            MotionPreset.PAN_LEFT,
                            MotionPreset.DRIFT_UP,
                        ][index % 5]
                    ),
                    entrance_motion="clean fade" if is_static else "gentle editorial ease-in",
                    focal_action=item.visual_intent,
                    transition_out=item.transition_to_next,
                    transition_preset=(
                        TransitionPreset.FADE
                        if index == len(script.scenes) - 1
                        else TransitionPreset.DISSOLVE
                    ),
                    visual_beats=build_default_visual_beats(
                        item.target_seconds,
                        is_video=not preserve_source,
                        motion_preset=(
                            MotionPreset.SLOW_PUSH
                            if is_static
                            else [
                                MotionPreset.SLOW_PUSH,
                                MotionPreset.PAN_RIGHT,
                                MotionPreset.PULL_BACK,
                                MotionPreset.PAN_LEFT,
                                MotionPreset.DRIFT_UP,
                            ][index % 5]
                        ),
                    ),
                )
            )
        logger.info("visual plan created provider=debug scenes=%s", len(scenes))
        plan = PresentationVisualPlan(
            creative_direction=script.creative_direction,
            scenes=scenes,
        )
        _validate_sequence(plan, script, document)
        return plan


def _payload(document: PresentationDocument, script: PresentationScript) -> dict[str, object]:
    source = {slide.number: slide for slide in document.slides}
    return {
        "presentation_title": document.title,
        "creative_direction": script.creative_direction.model_dump(mode="json"),
        "scenes": [
            {
                "scene_number": item.scene_number,
                "source_slide_numbers": item.source_slide_numbers,
                "media_mode": item.media_mode,
                "story_beat": item.story_beat,
                "visual_intent": item.visual_intent,
                "scene_purpose": item.scene_purpose,
                "relationship_to_thesis": item.relationship_to_thesis,
                "narrative_progress": item.narrative_progress,
                "transition_to_next": item.transition_to_next,
                "short_caption": item.short_caption,
                "source_pages": [
                    {
                        "slide_number": number,
                        "title": source[number].title,
                        "source_text": source[number].body_text,
                        "speaker_notes": source[number].speaker_notes,
                        "source_frame_suitable": source[number].source_frame_suitable,
                    }
                    for number in item.source_slide_numbers
                ],
                "narration": item.narration,
                "dialogue": [line.model_dump(mode="json") for line in item.dialogue],
                "duration_seconds": item.target_seconds,
                "critical_information": [
                    unit.model_dump(mode="json") for unit in item.critical_information
                ],
            }
            for item in script.scenes
        ],
    }


def validate_sequence(
    plan: PresentationVisualPlan,
    script: PresentationScript,
    document: PresentationDocument | None = None,
) -> None:
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
        scene.scene_purpose = script_scene.scene_purpose or scene.scene_purpose
        scene.relationship_to_thesis = (
            script_scene.relationship_to_thesis or scene.relationship_to_thesis
        )
        scene.narrative_progress = script_scene.narrative_progress or scene.narrative_progress
        scene.critical_information = list(script_scene.critical_information)
        inferred_concepts = infer_required_concepts(
            f"{script_scene.narration} {script_scene.visual_intent} {scene.prompt}"
        )
        if not scene.must_show_concepts:
            scene.must_show_concepts = inferred_concepts
        if not scene.concept_visualization:
            scene.concept_visualization = default_concept_visualization(scene.must_show_concepts)
        if scene.media_mode == MediaMode.STATIC:
            suitable_numbers = (
                {slide.number for slide in document.slides if slide.source_frame_suitable}
                if document is not None
                else set(scene.source_slide_numbers)
            )
            eligible = [
                number for number in scene.source_slide_numbers if number in suitable_numbers
            ]
            scene.preserve_source_frame = bool(eligible)
            if scene.preserve_source_frame:
                if scene.source_slide_number not in eligible:
                    scene.source_slide_number = eligible[0]
                scene.camera_motion = "none"
                scene.motion_preset = MotionPreset.NONE
            else:
                scene.source_slide_number = None
                scene.camera_motion = "subtle editorial camera movement"
                if scene.motion_preset == MotionPreset.NONE:
                    scene.motion_preset = MotionPreset.SLOW_PUSH
                scene.prompt = (
                    "Create a text-free editorial storytelling illustration grounded in the cited "
                    "source concepts. Never show, reproduce, photograph, scan, frame, or quote the "
                    "original document page or its paragraphs. "
                    f"Scene purpose: {scene.scene_purpose or script_scene.visual_intent}. "
                    f"Required narrative meaning: "
                    f"{scene.relationship_to_thesis or script_scene.relationship_to_thesis}. "
                    f"Previous visual request, usable only for subject matter: {scene.prompt}"
                )
        else:
            scene.source_slide_number = None
            scene.preserve_source_frame = False
            hard_exclusions = (
                "text, words, letters, numbers, typography, subtitles, captions, logos, signs, "
                "documents, slides, readable UI, fake interface text, public "
                "figures, recognizable people, politics, protests, crowds, conflict, weapons, "
                "military content, fire, smoke, explosions, injuries, emergencies, dangerous "
                "behavior, medical procedures, minors, nudity, sexual content"
            )
            if hard_exclusions not in scene.negative_prompt:
                scene.negative_prompt = f"{hard_exclusions}, {scene.negative_prompt}"
        scene.visual_beats = _normalize_visual_beats(scene, script_scene.target_seconds)
    # Narrative direction remains authoritative, while the visual planner owns the cast bible
    # because it is the stage that turns recurring roles into concrete on-screen identities.
    plan.creative_direction = script.creative_direction.model_copy(
        update={"characters": plan.creative_direction.characters}
    )


# Backwards-compatible alias for integrations that imported the former private helper.
_validate_sequence = validate_sequence


def _plan_issues(plan: PresentationVisualPlan, script: PresentationScript) -> list[str]:
    issues: list[str] = []
    character_ids = {character.id for character in plan.creative_direction.characters}
    scripts = {scene.scene_number: scene for scene in script.scenes}
    for scene in plan.scenes:
        unknown_characters = set(scene.recurring_character_ids) - character_ids
        if unknown_characters:
            issues.append(
                f"scene {scene.scene_number} references undefined recurring characters: "
                f"{', '.join(sorted(unknown_characters))}"
            )
        if not scene.scene_purpose.strip():
            issues.append(f"scene {scene.scene_number} has no scene_purpose")
        if not scene.relationship_to_thesis.strip():
            issues.append(f"scene {scene.scene_number} has no relationship_to_thesis")
        if not scene.narrative_progress.strip():
            issues.append(f"scene {scene.scene_number} has no narrative_progress")
        if not scene.visible_evidence:
            issues.append(f"scene {scene.scene_number} has no concrete visible_evidence")
        if (
            scene.media_mode == MediaMode.VIDEO or not scene.preserve_source_frame
        ) and not scene.forbidden_substitutions:
            issues.append(
                f"video scene {scene.scene_number} has no forbidden_substitutions to prevent "
                "a generic visual"
            )
        if scene.media_mode == MediaMode.VIDEO:
            required_actions = math.ceil(scripts[scene.scene_number].target_seconds / 8)
            if len(scene.action_progression) != required_actions:
                issues.append(
                    f"video scene {scene.scene_number} requires exactly {required_actions} "
                    f"chronological action_progression items, found "
                    f"{len(scene.action_progression)}"
                )
    return issues


def _normalize_visual_beats(
    scene: VisualScenePlan,
    duration_seconds: int,
) -> list[VisualBeat]:
    if scene.media_mode == MediaMode.STATIC and not scene.preserve_source_frame:
        return [
            VisualBeat(
                beat_number=1,
                kind=VisualBeatKind.GENERATED_IMAGE,
                duration_seconds=duration_seconds,
                motion_preset=scene.motion_preset,
                transition=scene.transition_preset,
            )
        ]
    beats = scene.visual_beats
    generated_videos = sum(beat.kind == VisualBeatKind.GENERATED_VIDEO for beat in beats)
    total = sum(beat.duration_seconds for beat in beats)
    if not beats or generated_videos > 1 or abs(total - duration_seconds) > 0.5:
        return build_default_visual_beats(
            duration_seconds,
            is_video=scene.media_mode == MediaMode.VIDEO,
            motion_preset=scene.motion_preset,
            allow_source_slide=scene.preserve_source_frame,
        )
    return [
        beat.model_copy(update={"beat_number": index}) for index, beat in enumerate(beats, start=1)
    ]


class PydanticAIVisualPlanner:
    def __init__(self, model: str, max_revisions: int = 2) -> None:
        self._agent = Agent(
            model,
            output_type=PresentationVisualPlan,
            system_prompt=_SYSTEM_PROMPT,
        )
        self._max_revisions = max_revisions

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        source_prompt = json.dumps(_payload(document, script), ensure_ascii=False)
        prompt = source_prompt
        for revision in range(self._max_revisions + 1):
            result = await self._agent.run(prompt)
            plan = result.output
            _validate_sequence(plan, script, document)
            issues = _plan_issues(plan, script)
            if not issues:
                return plan
            if revision == self._max_revisions:
                logger.error(
                    "visual plan remained semantically generic provider=pydantic_ai attempts=%s; "
                    "using grounded deterministic fallback",
                    revision + 1,
                )
                return await DebugVisualPlanner().plan(document, script)
            logger.warning(
                "visual plan revision requested provider=pydantic_ai attempt=%s issues=%s",
                revision + 1,
                issues,
            )
            prompt = (
                "Revise the complete visual plan. Correct every issue below while preserving the "
                "source facts and scene sequence. Return all scenes, not only changed scenes.\n- "
                + "\n- ".join(issues)
                + f"\n\nOriginal input:\n{source_prompt}\n\nPrevious plan:\n"
                + plan.model_dump_json()
            )
        raise RuntimeError("Unreachable visual planning revision state")


class ReplicateVisualPlanner:
    """Runs a JSON-capable LLM hosted by Replicate and validates its output with Pydantic."""

    def __init__(
        self,
        client: ReplicatePredictionClient,
        model: str,
        input_defaults: dict[str, object] | None = None,
        max_revisions: int = 2,
    ) -> None:
        self._client = client
        self._model = model
        self._input_defaults = input_defaults or {}
        self._max_revisions = max_revisions

    async def plan(
        self, document: PresentationDocument, script: PresentationScript
    ) -> PresentationVisualPlan:
        schema = json.dumps(PresentationVisualPlan.model_json_schema(), ensure_ascii=False)
        initial_prompt = (
            f"{_SYSTEM_PROMPT}\n\nReturn JSON only, matching this schema:\n{schema}\n\nInput:\n"
            f"{json.dumps(_payload(document, script), ensure_ascii=False)}"
        )
        prompt = initial_prompt
        for revision in range(self._max_revisions + 1):
            output = await self._client.run(
                self._model,
                {**self._input_defaults, "prompt": prompt},
            )
            text = self._strip_fence(self._client.output_text(output))
            try:
                plan = PresentationVisualPlan.model_validate_json(text)
            except ValidationError as exc:
                if revision == self._max_revisions:
                    logger.error(
                        "visual planning JSON remained invalid provider=replicate model=%s "
                        "attempts=%s; using grounded deterministic fallback",
                        self._model,
                        revision + 1,
                    )
                    return await DebugVisualPlanner().plan(document, script)
                summary = "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()[:8]
                )
                logger.warning(
                    "visual planning JSON correction requested provider=replicate model=%s "
                    "attempt=%s summary=%s",
                    self._model,
                    revision + 1,
                    summary,
                )
                prompt = (
                    "Repair the previous response. Return one complete, valid JSON object only: "
                    "no markdown fence, commentary, ellipsis, or trailing text. Preserve every "
                    "scene and match the supplied schema exactly.\n\n"
                    f"Validation errors:\n{summary}\n\nSchema:\n{schema}\n\n"
                    f"Original task:\n{initial_prompt}\n\nInvalid response:\n{text}"
                )
                continue
            _validate_sequence(plan, script, document)
            issues = _plan_issues(plan, script)
            if not issues:
                return plan
            if revision == self._max_revisions:
                logger.error(
                    "visual plan remained semantically generic provider=replicate model=%s "
                    "attempts=%s; using grounded deterministic fallback",
                    self._model,
                    revision + 1,
                )
                return await DebugVisualPlanner().plan(document, script)
            logger.warning(
                "visual plan semantic correction requested provider=replicate model=%s "
                "attempt=%s issues=%s",
                self._model,
                revision + 1,
                issues,
            )
            prompt = (
                "Revise the complete visual plan and return valid JSON only. Correct every issue "
                "below. Preserve the source facts, narrative grammar, and complete scene sequence. "
                "Return all scenes, not only changed scenes.\n- "
                + "\n- ".join(issues)
                + f"\n\nSchema:\n{schema}\n\nOriginal task:\n{initial_prompt}\n\n"
                f"Previous plan:\n{plan.model_dump_json()}"
            )
        raise RuntimeError("Unreachable visual planning retry state")

    @staticmethod
    def _strip_fence(value: str) -> str:
        value = value.strip()
        if value.startswith("```"):
            value = value.split("\n", 1)[-1]
            value = value.rsplit("```", 1)[0]
        return value.strip()
