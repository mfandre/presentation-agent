from __future__ import annotations

import json
import logging
import math

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Agent, UnexpectedModelBehavior

from presentation_video.domain.errors import NarrativeDurationError, NarrativeGenerationError
from presentation_video.domain.models import (
    MediaMode,
    PresentationDocument,
    PresentationScript,
    SceneScript,
)
from presentation_video.domain.ports import NarrativeGenerator
from presentation_video.infrastructure.replicate import ReplicatePredictionClient

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """
You are a senior presentation writer and speaker coach.
Create a coherent spoken narrative from the supplied presentation slides.

Rules:
- Preserve the original facts. Never invent metrics, dates, names, or conclusions.
- Do not merely read bullets; explain the meaning and transitions naturally.
- Build a coherent storytelling arc; never create one scene per page by default.
- Merge related pages into thematic scenes, omit repetitive/administrative pages, and split a
  dense topic only when that improves the explanation.
- Number output scenes consecutively from 1, independently from source page numbers.
- Every scene must cite the source_slide_numbers that ground its narration.
- Account for every source page: cite it in a scene or list it in omitted_source_slide_numbers when
  it is repetitive, administrative, empty, or irrelevant to the requested storytelling.
- Use the requested language, audience, and tone.
- Each scene narration must stand alone enough to be regenerated independently.
- Respect the speaking-rate budget supplied in the user prompt.
- Use a concise short_caption that reinforces the main message of the scene.
- Design an editorial rhythm that combines crisp static source frames with short, word-free video
  moments. This is a content-driven storyboard, not a mechanical alternation.
- Choose media_mode="static" when the audience must read or inspect exact words, numbers, tables,
  charts, quotes, decisions, architecture, or a key conclusion. The original source page will be
  shown unchanged, so visual_intent must identify what the viewer should notice there.
- Choose media_mode="video" for movement, atmosphere, real-world context, human action, physical
  process, or an illustrative demonstration. Its visual_intent must be fully understandable with
  no words, letters, numbers, logos, screens, captions, or signage visible.
- Every script must contain at least two scenes and both static and video modes. Avoid more than
  two video scenes in a row, and use static scenes as clear informational anchors.
- Give every scene a story_beat and a transition_to_next. Narration should hand off naturally from
  one scene to the next without announcing slide or scene numbers.
- Do not return scene durations. Timing is calculated deterministically by the application.
""".strip()


class _LLMSceneScript(BaseModel):
    """Narrative fields owned by the LLM; timing deliberately belongs to the backend."""

    model_config = ConfigDict(extra="ignore")

    scene_number: int = Field(ge=1)
    source_slide_numbers: list[int] = Field(min_length=1)
    narration: str = Field(min_length=1)
    short_caption: str = Field(default="", max_length=160)
    media_mode: MediaMode
    story_beat: str = Field(min_length=1, max_length=120)
    visual_intent: str = Field(min_length=1, max_length=500)
    transition_to_next: str = Field(min_length=1, max_length=300)


class _LLMPresentationScript(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    scenes: list[_LLMSceneScript] = Field(min_length=1)
    omitted_source_slide_numbers: list[int] = Field(default_factory=list)


def _narrative_payload(
    document: PresentationDocument,
    target_seconds: int,
    language: str,
    audience: str,
    tone: str,
) -> dict[str, object]:
    return {
        "requested_duration_seconds": target_seconds,
        "language": language,
        "audience": audience,
        "tone": tone,
        "presentation_title": document.title,
        "slides": [
            {
                "slide_number": slide.number,
                "title": slide.title,
                "body_text": slide.body_text,
                "speaker_notes": slide.speaker_notes,
            }
            for slide in document.slides
        ],
    }


def _script_issues(
    script: _LLMPresentationScript,
    document: PresentationDocument,
    target_seconds: int,
    words_per_minute: int,
) -> list[str]:
    issues: list[str] = []
    expected_scene_numbers = list(range(1, len(script.scenes) + 1))
    actual_scene_numbers = [scene.scene_number for scene in script.scenes]
    if actual_scene_numbers != expected_scene_numbers:
        issues.append(
            f"scene sequence is {actual_scene_numbers}; it must be exactly {expected_scene_numbers}"
        )
    if len(script.scenes) < 2:
        issues.append(
            "hybrid storyboard contains fewer than two scenes; it needs at least one static "
            "scene and one video scene"
        )
    else:
        media_modes = {scene.media_mode for scene in script.scenes}
        missing_modes = {MediaMode.STATIC, MediaMode.VIDEO} - media_modes
        if missing_modes:
            issues.append(
                "hybrid storyboard is missing media mode(s) "
                f"{sorted(mode.value for mode in missing_modes)}; use both static and video scenes"
            )
    consecutive_videos = 0
    for scene in script.scenes:
        consecutive_videos = consecutive_videos + 1 if scene.media_mode == MediaMode.VIDEO else 0
        if consecutive_videos > 2:
            issues.append("storyboard contains more than two consecutive video scenes")
            break
    valid_source_numbers = {slide.number for slide in document.slides}
    cited_source_numbers: set[int] = set()
    for scene in script.scenes:
        cited_source_numbers.update(scene.source_slide_numbers)
        invalid_sources = sorted(set(scene.source_slide_numbers) - valid_source_numbers)
        if invalid_sources:
            issues.append(
                f"scene {scene.scene_number} cites non-existent source slides {invalid_sources}"
            )
    omitted_source_numbers = set(script.omitted_source_slide_numbers)
    invalid_omissions = sorted(omitted_source_numbers - valid_source_numbers)
    if invalid_omissions:
        issues.append(f"omitted_source_slide_numbers contains invalid slides {invalid_omissions}")
    overlap = sorted(cited_source_numbers & omitted_source_numbers)
    if overlap:
        issues.append(f"source slides {overlap} are both cited and marked as omitted")
    unaccounted = sorted(valid_source_numbers - cited_source_numbers - omitted_source_numbers)
    if unaccounted:
        issues.append(
            f"source slides {unaccounted} are neither cited by a scene nor explicitly omitted"
        )
    if len(script.scenes) > target_seconds:
        issues.append(
            f"script contains {len(script.scenes)} scenes but the requested duration is only "
            f"{target_seconds} seconds"
        )
    maximum_words = math.floor(target_seconds * words_per_minute / 60)
    actual_words = sum(len(scene.narration.split()) for scene in script.scenes)
    if actual_words > maximum_words:
        issues.append(
            f"narration has {actual_words} words; the maximum budget is {maximum_words} words "
            f"at {words_per_minute} words per minute"
        )
    return issues


def _allocate_weighted_total(
    total: int, weights: list[int], minimum_per_item: int = 0
) -> list[int]:
    if not weights:
        return []
    reserved = minimum_per_item * len(weights)
    if total < reserved:
        raise ValueError(
            f"cannot allocate total={total} with minimum_per_item={minimum_per_item} "
            f"across {len(weights)} items"
        )
    remaining = total - reserved
    normalized_weights = [max(weight, 1) for weight in weights]
    total_weight = sum(normalized_weights)
    raw_allocations = [remaining * weight / total_weight for weight in normalized_weights]
    allocations = [minimum_per_item + math.floor(value) for value in raw_allocations]
    remainder = total - sum(allocations)
    order = sorted(
        range(len(weights)),
        key=lambda index: raw_allocations[index] - math.floor(raw_allocations[index]),
        reverse=True,
    )
    for index in order[:remainder]:
        allocations[index] += 1
    return allocations


def _duration_too_short_error(target_seconds: int, scene_count: int) -> NarrativeDurationError:
    return NarrativeDurationError(
        f"requested duration of {target_seconds}s cannot allocate at least one second "
        f"to each of {scene_count} narrative scenes",
        f"O tempo escolhido é muito curto para as {scene_count} cenas criadas para esta narrativa. "
        "Escolha uma duração maior e tente novamente.",
    )


def _ensure_duration_can_cover_scenes(target_seconds: int, scene_count: int) -> None:
    if target_seconds < scene_count:
        raise _duration_too_short_error(target_seconds, scene_count)


def _build_script(generated: _LLMPresentationScript, target_seconds: int) -> PresentationScript:
    _ensure_duration_can_cover_scenes(target_seconds, len(generated.scenes))
    weights = [max(len(scene.narration.split()), 1) for scene in generated.scenes]
    durations = _allocate_weighted_total(target_seconds, weights, minimum_per_item=1)
    return PresentationScript(
        title=generated.title,
        scenes=[
            SceneScript(
                scene_number=scene.scene_number,
                source_slide_numbers=sorted(set(scene.source_slide_numbers)),
                narration=scene.narration,
                short_caption=scene.short_caption,
                target_seconds=duration,
                media_mode=scene.media_mode,
                story_beat=scene.story_beat,
                visual_intent=scene.visual_intent,
                transition_to_next=scene.transition_to_next,
            )
            for scene, duration in zip(generated.scenes, durations, strict=True)
        ],
        omitted_source_slide_numbers=sorted(set(generated.omitted_source_slide_numbers)),
        total_estimated_seconds=target_seconds,
    )


def _source_page_count(payload: dict[str, object]) -> int:
    raw_slides = payload.get("slides")
    return len(raw_slides) if isinstance(raw_slides, list) else 0


def _scene_count_guidance(target_seconds: int) -> tuple[int, int, int]:
    minimum = max(2, math.ceil(target_seconds / 60))
    recommended = max(minimum, round(target_seconds / 30))
    maximum = max(recommended, math.floor(target_seconds / 20))
    return minimum, recommended, maximum


def _initial_prompt(payload: dict[str, object], target_seconds: int, words_per_minute: int) -> str:
    maximum_words = math.floor(target_seconds * words_per_minute / 60)
    target_words = math.floor(maximum_words * 0.9)
    source_page_count = _source_page_count(payload)
    minimum_scenes, recommended_scenes, maximum_scenes = _scene_count_guidance(target_seconds)
    return (
        f"Write the complete narration to fit within exactly {target_seconds} seconds or less.\n"
        f"Return exactly {target_words} spoken words in total and never exceed the hard "
        f"limit of {maximum_words} words, based on {words_per_minute} words per minute.\n"
        "Count words by whitespace-separated tokens before returning the JSON.\n"
        f"The source has {source_page_count} pages, but the output must not mirror that page count.\n"
        f"For this duration, aim for about {recommended_scenes} narrative scenes; a reasonable "
        f"range is {minimum_scenes} to {maximum_scenes} scenes. Choose the final count according "
        "to the story, not the number of pages.\n"
        "Create an opening, a logical development, and a conclusion. Merge pages that support "
        "the same idea, skip repeated agendas/appendices, and preserve all facts needed for the "
        "story to remain faithful to the source.\n"
        "For each scene, return consecutive scene_number and the source_slide_numbers used to "
        "ground that scene. A source page may support more than one scene when necessary.\n"
        "Build a fluid hybrid storyboard. Use static scenes as readable information anchors and "
        "video scenes as short word-free moments of movement, context, or demonstration. Create "
        "at least two scenes and include both media modes. Do not alternate mechanically; let the "
        "story determine the rhythm, but never place more than two video scenes consecutively.\n"
        "For every scene provide story_beat, visual_intent, and transition_to_next. Make each "
        "narration flow into the following beat without saying 'slide', 'page', or 'scene'.\n"
        "Keep static information anchors concise so the edit does not remain on one fixed frame "
        "for too long. Let longer explanatory passages breathe through word-free video beats.\n"
        "Every source page must be accounted for. Pages not used because they are repetitive, "
        "administrative, empty, or irrelevant must be listed in omitted_source_slide_numbers.\n"
        "Be concise from the first draft.\n"
        "Do not return target_seconds or total_estimated_seconds; the application derives them.\n\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _revision_prompt(
    payload: dict[str, object],
    script: _LLMPresentationScript,
    issues: list[str],
    target_seconds: int,
    words_per_minute: int,
) -> str:
    maximum_words = math.floor(target_seconds * words_per_minute / 60)
    target_words = math.floor(maximum_words * 0.9)
    actual_words = sum(len(scene.narration.split()) for scene in script.scenes)
    words_to_remove = max(actual_words - target_words, 0)
    minimum_scenes, recommended_scenes, maximum_scenes = _scene_count_guidance(target_seconds)
    return (
        "Revise the previous script instead of starting a different presentation.\n"
        "Correct every validation issue below:\n- "
        + "\n- ".join(issues)
        + f"\n\nHard requirements:\n"
        f"- The revised spoken narration must fit within {target_seconds} seconds.\n"
        f"- The complete narration must have at most {maximum_words} words in total.\n"
        f"- Return exactly {target_words} words in total; remove at least {words_to_remove} words "
        "from "
        f"the previous version, which has {actual_words} words.\n"
        f"- Aim for about {recommended_scenes} scenes, normally between {minimum_scenes} and "
        f"{maximum_scenes}; do not mirror the source page count.\n"
        "- Renumber scenes consecutively from 1 and cite only existing source_slide_numbers.\n"
        "- Merge related source pages and remove page-by-page repetition.\n"
        "- Return at least two scenes and preserve or correct a fluid hybrid rhythm using both "
        "media_mode=static and media_mode=video.\n"
        "- Never place more than two video scenes consecutively. Static scenes carry exact readable "
        "information; video scene visual_intent must contain no visible words or screens.\n"
        "- Return story_beat, visual_intent, and transition_to_next for every scene.\n"
        "- Account for every source page either in a scene or in omitted_source_slide_numbers.\n"
        "- Count the words in each rewritten scene narration before returning the JSON.\n"
        "- Condense wording and remove repetition; do not remove essential facts.\n"
        "- Preserve language, audience, tone, names, dates, metrics, and factual chronology.\n"
        "- Return the complete revised storytelling script, not only changed scenes.\n\n"
        "- Do not return target_seconds or total_estimated_seconds.\n\n"
        f"Original source:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"Previous script:\n{script.model_dump_json()}"
    )


def _final_validation_error(
    issues: list[str], attempts: int
) -> NarrativeDurationError | NarrativeGenerationError:
    technical_message = (
        f"LLM could not produce a valid script after {attempts} attempt(s): " + "; ".join(issues)
    )
    if any(
        issue.startswith("narration has") or "requested duration is only" in issue
        for issue in issues
    ):
        return NarrativeDurationError(
            technical_message,
            "O tempo escolhido é muito curto para o conteúdo da apresentação. "
            "Escolha uma duração maior e tente novamente.",
        )
    return NarrativeGenerationError(
        technical_message,
        "Não foi possível gerar um roteiro válido automaticamente. Tente novamente.",
    )


def _validation_error_summary(error: ValidationError, limit: int = 8) -> str:
    errors = error.errors(include_url=False, include_context=False, include_input=False)
    summaries: list[str] = []
    for issue in errors[:limit]:
        location = ".".join(str(part) for part in issue["loc"]) or "response"
        summaries.append(f"{location}: {issue['msg']}")
    remaining = len(errors) - len(summaries)
    if remaining:
        summaries.append(f"and {remaining} additional validation error(s)")
    return "; ".join(summaries)


def _structured_revision_prompt(
    failed_prompt: str,
    previous_response: str,
    validation_summary: str,
    schema: str,
) -> str:
    return (
        "Correct the previous response so it is valid JSON matching the schema. "
        "Keep the requested narrative content and return the complete JSON object only.\n\n"
        f"Validation errors:\n{validation_summary}\n\n"
        f"Schema:\n{schema}\n\n"
        f"Original task:\n{failed_prompt}\n\n"
        f"Previous invalid response:\n{previous_response}"
    )


def _narrative_generation_error(validation_summary: str, attempts: int) -> NarrativeGenerationError:
    return NarrativeGenerationError(
        f"LLM could not produce valid structured narrative JSON after {attempts} attempt(s): "
        f"{validation_summary}",
        "Não foi possível estruturar o roteiro automaticamente. Tente novamente.",
    )


class DebugNarrativeGenerator(NarrativeGenerator):
    """Creates a small deterministic script without calling an external model."""

    def __init__(self, max_scenes: int = 3, words_per_minute: int = 155) -> None:
        self._max_scenes = max_scenes
        self._words_per_minute = words_per_minute

    async def generate(
        self,
        document: PresentationDocument,
        target_seconds: int,
        language: str,
        audience: str,
        tone: str,
    ) -> PresentationScript:
        if self._max_scenes == 1:
            scene_count = 1
        else:
            scene_count = min(max(len(document.slides), 2), self._max_scenes, target_seconds)
        maximum_words = math.floor(target_seconds * self._words_per_minute / 60)
        target_words = max(scene_count, math.floor(maximum_words * 0.75))
        word_limits = _allocate_weighted_total(target_words, [1] * scene_count, 1)
        scenes: list[_LLMSceneScript] = []

        for index in range(scene_count):
            if len(document.slides) >= scene_count:
                start = index * len(document.slides) // scene_count
                end = (index + 1) * len(document.slides) // scene_count
                source_slides = document.slides[start:end]
            else:
                source_slides = [document.slides[index % len(document.slides)]]
            titles = [slide.title.strip() for slide in source_slides if slide.title.strip()]
            source_text = " ".join(
                part
                for slide in source_slides
                for part in (slide.title.strip(), slide.body_text.strip(), slide.speaker_notes.strip())
                if part
            )
            normalized = " ".join(source_text.split())
            if language.lower().startswith("pt"):
                prefix = "Nesta etapa, o documento apresenta"
                fallback = "os principais elementos desta parte da apresentação"
            else:
                prefix = "In this section, the document presents"
                fallback = "the main elements of this part of the presentation"
            narration_words = f"{prefix} {normalized or fallback}".split()[: word_limits[index]]
            is_last = index == scene_count - 1
            media_mode = (
                MediaMode.VIDEO
                if scene_count > 1 and index % 2 == 0 and not is_last
                else MediaMode.STATIC
            )
            story_beat = (
                "opening"
                if index == 0
                else "conclusion" if is_last else "development"
            )
            scenes.append(
                _LLMSceneScript(
                    scene_number=index + 1,
                    source_slide_numbers=[slide.number for slide in source_slides],
                    narration=" ".join(narration_words).rstrip(".,;:") + ".",
                    short_caption=(titles[0] if titles else f"Cena {index + 1}")[:160],
                    media_mode=media_mode,
                    story_beat=story_beat,
                    visual_intent=(
                        "Show a concrete, word-free real-world action that introduces the topic"
                        if media_mode == MediaMode.VIDEO
                        else "Keep the selected source page crisp so its exact information is readable"
                    ),
                    transition_to_next=(
                        "Resolve on the central takeaway and fade out"
                        if is_last
                        else "Connect the final idea directly to the next narrative beat"
                    ),
                )
            )

        script = _build_script(
            _LLMPresentationScript(title=document.title, scenes=scenes), target_seconds
        )
        logger.warning(
            "DEBUG_MODE active: deterministic narrative created without external calls "
            "source_pages=%s scenes=%s target_seconds=%s",
            len(document.slides),
            len(script.scenes),
            target_seconds,
        )
        return script


class PydanticAINarrativeGenerator(NarrativeGenerator):
    def __init__(self, model: str, max_revisions: int = 2, words_per_minute: int = 155) -> None:
        # Strategy is selected through configuration, e.g. openai:..., anthropic:..., google:...
        self._agent = Agent(
            model,
            output_type=_LLMPresentationScript,
            system_prompt=_SYSTEM_PROMPT,
            retries=max_revisions,
        )
        self._max_revisions = max_revisions
        self._words_per_minute = words_per_minute

    async def generate(
        self,
        document: PresentationDocument,
        target_seconds: int,
        language: str,
        audience: str,
        tone: str,
    ) -> PresentationScript:
        payload = _narrative_payload(document, target_seconds, language, audience, tone)
        maximum_words = math.floor(target_seconds * self._words_per_minute / 60)
        logger.info(
            "narrative generation started provider=pydantic_ai target_seconds=%s "
            "maximum_words=%s words_per_minute=%s source_pages=%s",
            target_seconds,
            maximum_words,
            self._words_per_minute,
            len(document.slides),
        )
        try:
            result = await self._agent.run(
                _initial_prompt(payload, target_seconds, self._words_per_minute)
            )
        except UnexpectedModelBehavior as exc:
            raise NarrativeGenerationError(
                f"Pydantic AI could not produce structured narrative output: {exc}",
                "Não foi possível estruturar o roteiro automaticamente. Tente novamente.",
            ) from exc
        script = result.output
        for revision in range(self._max_revisions + 1):
            issues = _script_issues(script, document, target_seconds, self._words_per_minute)
            if not issues:
                final_script = _build_script(script, target_seconds)
                logger.info(
                    "narrative accepted provider=pydantic_ai target_seconds=%s "
                    "estimated_seconds=%s words=%s revisions=%s min_scene_seconds=%s "
                    "max_scene_seconds=%s",
                    target_seconds,
                    final_script.total_estimated_seconds,
                    sum(len(scene.narration.split()) for scene in script.scenes),
                    revision,
                    min(scene.target_seconds for scene in final_script.scenes),
                    max(scene.target_seconds for scene in final_script.scenes),
                )
                return final_script
            if revision == self._max_revisions:
                raise _final_validation_error(issues, revision + 1)
            logger.warning(
                "narrative revision requested provider=pydantic_ai attempt=%s issues=%s",
                revision + 1,
                issues,
            )
            try:
                result = await self._agent.run(
                    _revision_prompt(
                        payload, script, issues, target_seconds, self._words_per_minute
                    )
                )
            except UnexpectedModelBehavior as exc:
                raise NarrativeGenerationError(
                    f"Pydantic AI could not revise structured narrative output: {exc}",
                    "Não foi possível corrigir o roteiro automaticamente. Tente novamente.",
                ) from exc
            script = result.output
        raise RuntimeError("Unreachable narrative revision state")


class ReplicateNarrativeGenerator(NarrativeGenerator):
    """Generates the structured narration with a JSON-capable LLM hosted by Replicate."""

    def __init__(
        self,
        client: ReplicatePredictionClient,
        model: str,
        input_defaults: dict[str, object] | None = None,
        max_revisions: int = 2,
        words_per_minute: int = 155,
    ) -> None:
        self._client = client
        self._model = model
        self._input_defaults = input_defaults or {}
        self._max_revisions = max_revisions
        self._words_per_minute = words_per_minute

    async def generate(
        self,
        document: PresentationDocument,
        target_seconds: int,
        language: str,
        audience: str,
        tone: str,
    ) -> PresentationScript:
        payload = _narrative_payload(document, target_seconds, language, audience, tone)
        maximum_words = math.floor(target_seconds * self._words_per_minute / 60)
        logger.info(
            "narrative generation started provider=replicate model=%s target_seconds=%s "
            "maximum_words=%s words_per_minute=%s source_pages=%s",
            self._model,
            target_seconds,
            maximum_words,
            self._words_per_minute,
            len(document.slides),
        )
        schema = json.dumps(_LLMPresentationScript.model_json_schema(), ensure_ascii=False)
        prompt = (
            f"{_SYSTEM_PROMPT}\n\nReturn JSON only, matching this schema:\n{schema}\n\n"
            f"{_initial_prompt(payload, target_seconds, self._words_per_minute)}"
        )
        for revision in range(self._max_revisions + 1):
            failed_prompt = prompt
            text = await self._run_json_text(prompt)
            try:
                script = _LLMPresentationScript.model_validate_json(text)
            except ValidationError as exc:
                summary = _validation_error_summary(exc)
                if revision == self._max_revisions:
                    raise _narrative_generation_error(summary, revision + 1) from exc
                logger.warning(
                    "narrative structured-output correction requested provider=replicate "
                    "attempt=%s validation_errors=%s summary=%s",
                    revision + 1,
                    exc.error_count(),
                    summary,
                )
                prompt = _structured_revision_prompt(failed_prompt, text, summary, schema)
                continue
            issues = _script_issues(script, document, target_seconds, self._words_per_minute)
            if not issues:
                final_script = _build_script(script, target_seconds)
                logger.info(
                    "narrative accepted provider=replicate target_seconds=%s "
                    "estimated_seconds=%s words=%s revisions=%s min_scene_seconds=%s "
                    "max_scene_seconds=%s",
                    target_seconds,
                    final_script.total_estimated_seconds,
                    sum(len(scene.narration.split()) for scene in script.scenes),
                    revision,
                    min(scene.target_seconds for scene in final_script.scenes),
                    max(scene.target_seconds for scene in final_script.scenes),
                )
                return final_script
            if revision == self._max_revisions:
                raise _final_validation_error(issues, revision + 1)
            logger.warning(
                "narrative revision requested provider=replicate attempt=%s issues=%s",
                revision + 1,
                issues,
            )
            prompt = (
                _revision_prompt(payload, script, issues, target_seconds, self._words_per_minute)
                + f"\n\nReturn JSON only, matching this schema:\n{schema}"
            )
        raise RuntimeError("Unreachable narrative revision state")

    async def _run_json_text(self, prompt: str) -> str:
        output = await self._client.run(self._model, {**self._input_defaults, "prompt": prompt})
        text = self._client.output_text(output).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return text
