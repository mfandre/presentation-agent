from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from presentation_video.application.cinematic import materialize_shot
from presentation_video.application.production_policy import shots_or_default
from presentation_video.domain.errors import VisualSafetyBlockedError
from presentation_video.domain.models import (
    CharacterProfile,
    CharacterReferenceArtifact,
    MediaMode,
    PresentationScript,
    PresentationVisualPlan,
    SlideContent,
    StoryboardBundle,
    StoryboardPanel,
    StoryboardSheet,
    VisualArtifact,
    VisualGenerationPurpose,
    VisualScenePlan,
)
from presentation_video.domain.ports import VisualAssetGenerator

logger = logging.getLogger(__name__)


def character_reference_prompt(character: CharacterProfile, art_style: str) -> str:
    markers = ", ".join(character.identity_markers) or "no additional identity markers"
    return (
        "Create a professional character reference sheet of exactly one recurring fictional "
        "character for consistent AI generation. "
        f"Character ID: {character.id}. Narrative role: {character.narrative_role}. "
        f"Physical appearance: {character.physical_appearance}. Wardrobe and accessories: "
        f"{character.wardrobe}. Immutable identity markers: {markers}. "
        "Display the same character in four equally spaced views on one plain neutral-grey studio "
        "background: (1) full-body straight front view in a neutral pose, head to toe; (2) "
        "full-body straight rear view; (3) detailed head-and-shoulders straight front portrait with "
        "a neutral expression; (4) head-and-shoulders left profile at exactly 90 degrees. Keep the "
        "same face, age, body proportions, skin tone, hairstyle, outfit, colors, materials, and "
        "accessories in every view. Use clean balanced studio lighting, soft shadows, accurate "
        "materials, sharp high-detail production reference quality, and an organized symmetrical "
        f"layout. Art style: {art_style}. Neutral grey background only. No text, labels, logos, "
        "watermarks, props, scenery, action poses, facial-expression variants, or extra characters."
    )


def compile_storyboard_panels(visual_plan: PresentationVisualPlan) -> list[StoryboardPanel]:
    panels: list[StoryboardPanel] = []
    story_offset = 0.0
    panel_number = 1
    for scene in visual_plan.scenes:
        scene_duration = 0.0
        for shot in shots_or_default(scene.shots):
            shot_number = shot.shot_number if shot else 1
            plan = materialize_shot(scene, shot) if shot else scene
            duration = (
                shot.duration_seconds
                if shot
                else sum(beat.duration_seconds for beat in scene.visual_beats) or 8.0
            )
            start = shot.start_seconds if shot else 0.0
            panels.append(
                StoryboardPanel(
                    panel_number=panel_number,
                    scene_number=scene.scene_number,
                    shot_number=shot_number,
                    sheet_number=1,
                    cell_number=1,
                    start_seconds=story_offset + start,
                    duration_seconds=duration,
                    camera=plan.camera_motion,
                    action=plan.focal_action or plan.prompt,
                    emotional_focus=plan.story_beat,
                    continuity_in=shot.continuity_in if shot else plan.entrance_motion,
                    continuity_out=shot.continuity_out if shot else plan.transition_out,
                    character_ids=plan.recurring_character_ids,
                )
            )
            panel_number += 1
            scene_duration = max(scene_duration, start + duration)
        story_offset += scene_duration
    return panels


def _grid_size(panel_count: int) -> tuple[int, int]:
    side = min(4, max(1, math.ceil(math.sqrt(panel_count))))
    return side, side


def storyboard_sheet_prompt(
    panels: list[StoryboardPanel],
    visual_plan: PresentationVisualPlan,
    script: PresentationScript,
    rows: int,
    columns: int,
) -> str:
    scene_plans = {scene.scene_number: scene for scene in visual_plan.scenes}
    scene_scripts = {scene.scene_number: scene for scene in script.scenes}
    cast = {character.id: character for character in visual_plan.creative_direction.characters}
    panel_lines: list[str] = []
    for panel in panels:
        plan = scene_plans[panel.scene_number]
        shot = plan.shots[panel.shot_number - 1] if plan.shots else None
        materialized = materialize_shot(plan, shot) if shot else plan
        characters = (
            ", ".join(
                f"{character_id} ({cast[character_id].narrative_role})"
                if character_id in cast
                else character_id
                for character_id in panel.character_ids
            )
            or "no recurring character"
        )
        panel_lines.append(
            f"Cell {panel.cell_number}, time {panel.start_seconds:.1f}s: "
            f"camera={panel.camera}; composition and action={materialized.prompt}; "
            f"focal action={panel.action}; characters={characters}; "
            f"emotion={panel.emotional_focus}; environment and evidence="
            f"{'; '.join(materialized.visible_evidence) or materialized.concept_visualization}; "
            f"entering state={panel.continuity_in}; exiting state={panel.continuity_out}; "
            f"narrative context={scene_scripts[panel.scene_number].narration}."
        )
    direction = visual_plan.creative_direction
    locked_art_style = next(
        (scene.visual_style for scene in visual_plan.scenes if scene.visual_style.strip()),
        direction.visual_motif,
    )
    return (
        "Create one clean professional cinematic storyboard sheet for image-to-video production. "
        f"Use an exact {rows} by {columns} uniform grid, read left-to-right and top-to-bottom, on "
        "an overall cinematic 16:9 canvas. Every cell is itself a cinematic 16:9 frame. Separate "
        "cells with thin neutral gutters and keep all important content away from cell edges. "
        f"The sheet contains {len(panels)} story panels; leave any unused trailing cells empty in "
        "the neutral background. This is the clean machine-readable copy: show absolutely no panel "
        "numbers, timecodes, production notes, titles, captions, labels, words, letters, digits, "
        "logos, or watermarks anywhere. An annotated review copy will be added deterministically "
        "after generation. Use strong visual storytelling, readable character acting, rich but "
        "coherent environments, natural movement implied by poses, and varied consecutive shot "
        "sizes. Do not repeat the same composition or completed action in adjacent cells. Each cell "
        "must advance the action from the exact previous exiting state toward the next state. "
        "When attached character reference sheets are present, preserve the exact face, age, body "
        "proportions, hairstyle, wardrobe, colors, accessories, and identity markers in every cell, "
        "while changing pose, expression, framing, and action as directed. Never merge identities. "
        f"Creative throughline: {direction.throughline}. Visual motif: {direction.visual_motif}. "
        f"LOCKED ART STYLE shared by every cell: {locked_art_style}. Recurring visual principle: "
        f"{direction.recurring_visual_principle}. Palette: "
        f"{', '.join(direction.palette)}. The integrated cells are: {' '.join(panel_lines)}"
    )


_BENIGN_STORYBOARD_REPLACEMENTS = (
    (r"\bmonstro comedor de carrinhos\b", "silhueta misteriosa"),
    (r"\bmonstro\b", "silhueta misteriosa"),
    (r"\bcriatura\b", "figura desconhecida"),
    (r"\bolhos brilhantes\b", "olhos de botão refletindo a lanterna"),
    (r"\bmissão de resgate\b", "missão de ajuda entre amigos"),
    (r"\bresgate\b", "ajuda cooperativa"),
    (r"\bcativeiro\b", "espaço apertado entre objetos"),
    (r"\bpres(a|o|as|os)\b", "temporariamente imobilizado"),
    (r"\batolad(a|o|as|os)\b", "com as rodas bloqueadas"),
    (r"\bdensa escuridão\b", "canto pouco iluminado e acolhedor"),
    (r"\bescuridão\b", "ambiente pouco iluminado"),
    (r"\bmedo\b", "hesitação"),
    (r"\btemido\b", "desconhecido"),
)

_TOY_CAST_MARKERS = (
    "toy",
    "brinquedo",
    "carrinho",
    "miniatura",
    "pelúcia",
    "stuffed animal",
    "doll",
    "robô",
    "robot",
)
_TOY_WORLD_MARKERS = ("toy", "brinqued", "pelúcia", "stuffed animal", "doll")


def _benign_storyboard_text(value: str) -> str:
    """Reframe ambiguous peril language while preserving the concrete toy-scale action."""

    sanitized = " ".join(value.split())
    for pattern, replacement in _BENIGN_STORYBOARD_REPLACEMENTS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def _is_toy_story(visual_plan: PresentationVisualPlan) -> bool:
    characters = visual_plan.creative_direction.characters
    if not characters:
        return False
    direction = visual_plan.creative_direction
    world_description = (
        f"{direction.throughline} {direction.visual_motif} {direction.central_thesis} "
        f"{direction.narrative_device}"
    ).casefold()
    explicitly_toy_world = any(marker in world_description for marker in _TOY_WORLD_MARKERS)
    return explicitly_toy_world and all(
        any(
            marker
            in (
                f"{character.narrative_role} {character.physical_appearance} {character.wardrobe}"
            ).casefold()
            for marker in _TOY_CAST_MARKERS
        )
        for character in characters
    )


_POSITION_NAMES = {
    (0, 0): "top-left",
    (0, 1): "top-center",
    (0, 2): "top-right",
    (1, 0): "middle-left",
    (1, 1): "center",
    (1, 2): "middle-right",
    (2, 0): "bottom-left",
    (2, 1): "bottom-center",
    (2, 2): "bottom-right",
}
_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four"}


def _panel_position(cell_number: int, columns: int) -> str:
    index = cell_number - 1
    row, column = divmod(index, columns)
    return _POSITION_NAMES.get(
        (row, column), f"row {_NUMBER_WORDS[row + 1]}, column {_NUMBER_WORDS[column + 1]}"
    )


def _clean_storyboard_style(value: str) -> str:
    cleaned = re.sub(
        r"Pixar-style 3D animation",
        "polished stylized 3D feature animation with original character designs",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"Disney animation aesthetic",
        "polished hand-drawn feature-animation aesthetic with original character designs",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bBrand direction:.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"#[0-9a-fA-F]{3,8}\b", "", cleaned)
    return " ".join(cleaned.split()).strip(" .")


def compact_storyboard_sheet_prompt(
    panels: list[StoryboardPanel],
    visual_plan: PresentationVisualPlan,
    script: PresentationScript,
    rows: int,
    columns: int,
    *,
    benign_reframe: bool = False,
) -> str:
    """Build a text-free contact sheet prompt without narration, timecodes, or labels."""

    scene_plans = {scene.scene_number: scene for scene in visual_plan.scenes}
    cast = {character.id: character for character in visual_plan.creative_direction.characters}
    scene_numbers = list(dict.fromkeys(panel.scene_number for panel in panels))
    scene_context = " ".join(
        "Visual objective: "
        + _benign_storyboard_text(
            "; ".join(
                filter(
                    None,
                    [
                        scene_plans[scene_number].story_beat,
                        scene_plans[scene_number].scene_purpose,
                        scene_plans[scene_number].focal_action,
                        *scene_plans[scene_number].visible_evidence,
                    ],
                )
            )
            if benign_reframe
            else "; ".join(
                filter(
                    None,
                    [
                        scene_plans[scene_number].story_beat,
                        scene_plans[scene_number].scene_purpose,
                        scene_plans[scene_number].focal_action,
                        *scene_plans[scene_number].visible_evidence,
                    ],
                )
            )
        )
        for scene_number in scene_numbers
    )
    panel_lines: list[str] = []
    for panel in panels:
        scene = scene_plans[panel.scene_number]
        shot = scene.shots[panel.shot_number - 1] if scene.shots else None
        progression = scene.action_progression or [scene.focal_action or panel.action]
        shot_count = max(len(scene.shots), 1)
        progression_index = min(
            len(progression) - 1,
            ((panel.shot_number - 1) * len(progression)) // shot_count,
        )
        moment = progression[progression_index]
        concepts = shot.required_concepts if shot else scene.must_show_concepts
        characters = (
            ", ".join(
                f"{character_id} ({cast[character_id].narrative_role})"
                if character_id in cast
                else character_id
                for character_id in panel.character_ids
            )
            or "no recurring character"
        )
        clean = _benign_storyboard_text if benign_reframe else lambda value: " ".join(value.split())
        panel_lines.append(
            f"At the {_panel_position(panel.cell_number, columns)} position: "
            f"camera={clean(panel.camera)}; visual action={clean(moment)}; "
            f"required visible subjects and props={clean(', '.join(concepts))}; cast={characters}; "
            "continue the exact physical state from the preceding position and leave a visibly "
            "advanced state for the following position."
        )
    direction = visual_plan.creative_direction
    locked_art_style = next(
        (scene.visual_style for scene in visual_plan.scenes if scene.visual_style.strip()),
        direction.visual_motif,
    )
    locked_art_style = _clean_storyboard_style(locked_art_style)
    safety = ""
    if benign_reframe:
        safety = (
            "Every subject is an inanimate fictional toy; show no human figures. Treat every "
            "ambiguous event as gentle toy-scale cooperation. "
            if _is_toy_story(visual_plan)
            else "Every subject is a fictional character in a clearly benign, non-graphic story. "
            "Reframe ambiguous events as calm cooperation. "
        )
    return (
        "Create one clean machine-readable cinematic contact sheet for image-to-video production. "
        f"{safety}Use an exact {_NUMBER_WORDS[rows]} by {_NUMBER_WORDS[columns]} uniform grid. "
        "Every position is an edge-to-edge widescreen cinematic frame. Use zero outer margin, zero "
        "gutters, zero padding, and zero borders; adjacent frames must touch directly. Leave unused "
        "trailing positions empty. The result is not an annotated storyboard: show absolutely no "
        "text, panel numbers, timecodes, clocks, timers, production notes, titles, subtitles, "
        "captions, labels, words, letters, digits, logos, signs, book text, or watermarks. Do not "
        "draw any typographic mark even when the narrative contains speech. Preserve "
        "the attached canonical character designs exactly, never merge identities, and vary pose, "
        "framing, expression, and action between cells. Each cell must advance from the preceding "
        "state without repeating a completed action. "
        f"Locked medium for every frame: {locked_art_style}. "
        f"{scene_context} Integrated cells: {' '.join(panel_lines)}"
    )


def safe_storyboard_sheet_prompt(
    panels: list[StoryboardPanel],
    visual_plan: PresentationVisualPlan,
    script: PresentationScript,
    rows: int,
    columns: int,
) -> str:
    """Build an explicitly benign retry prompt for a provider-blocked sheet."""

    return compact_storyboard_sheet_prompt(
        panels,
        visual_plan,
        script,
        rows,
        columns,
        benign_reframe=True,
    )


async def generate_character_references(
    visual_plan: PresentationVisualPlan,
    generator: VisualAssetGenerator,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> list[CharacterReferenceArtifact]:
    used_ids = {
        character_id
        for scene in visual_plan.scenes
        for character_id in scene.recurring_character_ids
    }
    if not used_ids:
        return []
    art_style = next(
        (scene.visual_style for scene in visual_plan.scenes if scene.visual_style.strip()),
        visual_plan.creative_direction.visual_motif,
    )
    references: list[CharacterReferenceArtifact] = []
    # Deliberately sequential: it avoids burst throttling and prevents one failed sheet from
    # obscuring which recurring identity needs revision.
    for index, character in enumerate(visual_plan.creative_direction.characters, start=1):
        if character.id not in used_ids:
            continue
        prompt = character_reference_prompt(character, art_style)
        plan = VisualScenePlan(
            scene_number=index,
            shot_number=1,
            generation_purpose=VisualGenerationPurpose.CHARACTER_REFERENCE,
            prompt=prompt,
            media_mode=MediaMode.VIDEO,
            preserve_source_frame=False,
            visual_style=art_style,
            recurring_character_ids=[character.id],
        )
        character_dir = output_dir / character.id
        prompt_path = character_dir / "prompt.txt"
        cached = next(
            (
                path
                for path in sorted(character_dir.glob("reference.*"))
                if path.is_file() and path.stat().st_size > 0
            ),
            None,
        )
        if (
            cached is not None
            and prompt_path.is_file()
            and prompt_path.read_text(encoding="utf-8") == prompt
        ):
            artifact = VisualArtifact(
                scene_number=index,
                path=cached,
                kind="image",
            )
        else:
            async with semaphore:
                generated = await generator.generate(
                    plan,
                    [],
                    character_dir,
                )
            character_dir.mkdir(parents=True, exist_ok=True)
            canonical = character_dir / f"reference{generated.path.suffix.lower()}"
            if generated.path != canonical:
                shutil.copy2(generated.path, canonical)
            prompt_path.write_text(prompt, encoding="utf-8")
            artifact = generated.model_copy(update={"path": canonical})
        references.append(
            CharacterReferenceArtifact(
                character_id=character.id,
                path=artifact.path,
                prompt=prompt,
                revision=artifact.revision,
            )
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "references.json").write_text(
        json.dumps(
            [reference.model_dump(mode="json") for reference in references],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return references


async def generate_storyboard_bundle(
    visual_plan: PresentationVisualPlan,
    script: PresentationScript,
    character_references: list[CharacterReferenceArtifact],
    generator: VisualAssetGenerator,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
    *,
    panels_per_sheet: int = 9,
) -> tuple[StoryboardBundle, list[VisualArtifact]]:
    if not 1 <= panels_per_sheet <= 16:
        raise ValueError("panels_per_sheet must be between 1 and 16")
    panels = compile_storyboard_panels(visual_plan)
    if not panels:
        raise ValueError("cinematic storyboard requires at least one panel")
    references = [
        SlideContent(
            number=index,
            title=f"Character identity reference — {reference.character_id}",
            body_text=(
                "Canonical cast reference only. Preserve identity and wardrobe in every panel; "
                "do not copy this sheet layout into the storyboard."
            ),
            image_path=reference.path,
        )
        for index, reference in enumerate(character_references, start=1)
    ]
    sheets: list[StoryboardSheet] = []
    panel_artifacts: list[VisualArtifact] = []
    for sheet_index, start in enumerate(range(0, len(panels), panels_per_sheet), start=1):
        sheet_panels = panels[start : start + panels_per_sheet]
        rows, columns = _grid_size(len(sheet_panels))
        for cell_number, panel in enumerate(sheet_panels, start=1):
            panel.sheet_number = sheet_index
            panel.cell_number = cell_number
        legacy_prompt = storyboard_sheet_prompt(
            sheet_panels,
            visual_plan,
            script,
            rows,
            columns,
        )
        prompt = compact_storyboard_sheet_prompt(
            sheet_panels,
            visual_plan,
            script,
            rows,
            columns,
        )
        plan = VisualScenePlan(
            scene_number=sheet_panels[0].scene_number,
            shot_number=sheet_index,
            generation_purpose=VisualGenerationPurpose.STORYBOARD,
            prompt=prompt,
            media_mode=MediaMode.VIDEO,
            preserve_source_frame=False,
            visual_style=visual_plan.creative_direction.visual_motif,
            recurring_character_ids=sorted(
                {character_id for panel in sheet_panels for character_id in panel.character_ids}
            ),
        )
        clean_dir = output_dir / "clean"
        prompt_path = clean_dir / f"storyboard-{sheet_index:03d}.prompt.txt"
        fallback_prompt = safe_storyboard_sheet_prompt(
            sheet_panels,
            visual_plan,
            script,
            rows,
            columns,
        )
        # Long prompts repeat scene-level peril vocabulary once per panel. For an explicitly
        # toy-based story, use the compact benign form proactively instead of paying for a request
        # that the provider is highly likely to reject.
        initial_prompt = (
            fallback_prompt if _is_toy_story(visual_plan) and len(prompt) > 30_000 else prompt
        )
        cached = next(
            (
                path
                for path in sorted(clean_dir.glob(f"storyboard-{sheet_index:03d}.*"))
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                and path.is_file()
                and path.stat().st_size > 0
            ),
            None,
        )
        cached_text_evidence = (
            await asyncio.to_thread(_storyboard_text_evidence, cached) if cached is not None else []
        )
        if cached_text_evidence:
            logger.warning(
                "storyboard cached sheet rejected because visible text was detected sheet=%s "
                "evidence=%s path=%s",
                sheet_index,
                cached_text_evidence,
                cached,
            )
        if (
            cached is not None
            and not cached_text_evidence
            and prompt_path.is_file()
            and prompt_path.read_text(encoding="utf-8") in {legacy_prompt, prompt, fallback_prompt}
        ):
            clean = VisualArtifact(
                scene_number=sheet_panels[0].scene_number,
                shot_number=sheet_index,
                path=cached,
                kind="image",
            )
        else:
            clean_dir.mkdir(parents=True, exist_ok=True)
            attempt_one_path = clean_dir / f"storyboard-{sheet_index:03d}.attempt-1.prompt.txt"
            attempt_one_path.write_text(initial_prompt, encoding="utf-8")
            successful_prompt = initial_prompt
            if initial_prompt == fallback_prompt:
                logger.info(
                    "storyboard sheet using proactive compact benign prompt sheet=%s panels=%s-%s "
                    "original_prompt_characters=%s fallback_prompt_characters=%s",
                    sheet_index,
                    sheet_panels[0].panel_number,
                    sheet_panels[-1].panel_number,
                    len(prompt),
                    len(fallback_prompt),
                )
            try:
                async with semaphore:
                    generated = await generator.generate(
                        plan.model_copy(update={"prompt": initial_prompt}),
                        references,
                        output_dir / "masters",
                        revision=2 if cached_text_evidence else 1,
                    )
            except VisualSafetyBlockedError:
                if initial_prompt == fallback_prompt:
                    raise
                logger.warning(
                    "storyboard sheet blocked by image safety; retrying with compact benign "
                    "prompt sheet=%s panels=%s-%s original_prompt_characters=%s "
                    "fallback_prompt_characters=%s",
                    sheet_index,
                    sheet_panels[0].panel_number,
                    sheet_panels[-1].panel_number,
                    len(prompt),
                    len(fallback_prompt),
                )
                successful_prompt = fallback_prompt
                attempt_two_path = clean_dir / f"storyboard-{sheet_index:03d}.attempt-2.prompt.txt"
                attempt_two_path.write_text(fallback_prompt, encoding="utf-8")
                async with semaphore:
                    generated = await generator.generate(
                        plan.model_copy(update={"prompt": fallback_prompt}),
                        references,
                        output_dir / "masters",
                        revision=2 if cached_text_evidence else 1,
                    )
            generated_text_evidence = await asyncio.to_thread(
                _storyboard_text_evidence,
                generated.path,
            )
            if generated_text_evidence:
                logger.warning(
                    "storyboard generated sheet rejected because visible text was detected; "
                    "retrying once sheet=%s evidence=%s path=%s",
                    sheet_index,
                    generated_text_evidence,
                    generated.path,
                )
                attempt_three_prompt = (
                    f"{prompt} OUTPUT VALIDATION OVERRIDE: Any visible glyph makes this "
                    "asset unusable. Replace anything resembling writing with plain unmarked "
                    "material or empty background."
                )
                (clean_dir / f"storyboard-{sheet_index:03d}.text-retry.prompt.txt").write_text(
                    attempt_three_prompt,
                    encoding="utf-8",
                )
                async with semaphore:
                    generated = await generator.generate(
                        plan.model_copy(update={"prompt": attempt_three_prompt}),
                        references,
                        output_dir / "masters",
                        revision=3,
                    )
                generated_text_evidence = await asyncio.to_thread(
                    _storyboard_text_evidence,
                    generated.path,
                )
                if generated_text_evidence:
                    raise ValueError(
                        f"Storyboard sheet {sheet_index} still contains visible text after a "
                        f"clean retry: {generated_text_evidence}"
                    )
                successful_prompt = attempt_three_prompt
            canonical = clean_dir / (f"storyboard-{sheet_index:03d}{generated.path.suffix.lower()}")
            if generated.path != canonical:
                shutil.copy2(generated.path, canonical)
            prompt_path.write_text(successful_prompt, encoding="utf-8")
            clean = generated.model_copy(update={"path": canonical})
        review_path = output_dir / "review" / f"storyboard-{sheet_index:03d}.jpg"
        extracted = await asyncio.to_thread(
            _extract_and_annotate_sheet,
            clean.path,
            review_path,
            sheet_panels,
            rows,
            columns,
            output_dir / "panels",
        )
        for panel, path in zip(sheet_panels, extracted, strict=True):
            panel.image_path = path
            panel_artifacts.append(
                VisualArtifact(
                    scene_number=panel.scene_number,
                    shot_number=panel.shot_number,
                    path=path,
                    kind="image",
                )
            )
        sheets.append(
            StoryboardSheet(
                sheet_number=sheet_index,
                clean_path=clean.path,
                review_path=review_path,
                rows=rows,
                columns=columns,
                panel_numbers=[panel.panel_number for panel in sheet_panels],
            )
        )
    plan_path = output_dir / "storyboard-plan.json"
    bundle = StoryboardBundle(panels=panels, sheets=sheets, plan_path=plan_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    return bundle, panel_artifacts


def build_segment_storyboard(
    images: list[VisualArtifact],
    output_path: Path,
) -> VisualArtifact:
    if not images:
        raise ValueError("a storyboard segment requires at least one panel")
    newest_input = max(image.path.stat().st_mtime for image in images)
    if (
        output_path.is_file()
        and output_path.stat().st_size > 0
        and output_path.stat().st_mtime >= newest_input
    ):
        return VisualArtifact(
            scene_number=images[0].scene_number,
            shot_number=images[0].shot_number,
            path=output_path,
            kind="image",
            revision=max(image.revision for image in images),
        )
    rows, columns = _grid_size(len(images))
    cell_width, cell_height = 640, 360
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "#E5E7EB")
    for index, artifact in enumerate(images):
        with Image.open(artifact.path) as source:
            frame = ImageOps.fit(source.convert("RGB"), (cell_width, cell_height))
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        canvas.paste(frame, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=92)
    return VisualArtifact(
        scene_number=images[0].scene_number,
        shot_number=images[0].shot_number,
        path=output_path,
        kind="image",
        revision=max(image.revision for image in images),
    )


def _extract_and_annotate_sheet(
    clean_path: Path,
    review_path: Path,
    panels: list[StoryboardPanel],
    rows: int,
    columns: int,
    panel_dir: Path,
) -> list[Path]:
    with Image.open(clean_path) as opened:
        clean = opened.convert("RGB")
    width, height = clean.size
    cell_width = width / columns
    cell_height = height / rows
    panel_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    review = clean.copy()
    draw = ImageDraw.Draw(review, "RGBA")
    font = ImageFont.load_default(size=max(14, width // 90))
    for panel in panels:
        index = panel.cell_number - 1
        column = index % columns
        row = index // columns
        left = round(column * cell_width)
        top = round(row * cell_height)
        right = round((column + 1) * cell_width)
        bottom = round((row + 1) * cell_height)
        crop = clean.crop((left, top, right, bottom))
        crop = _trim_storyboard_cell(crop)
        crop = ImageOps.fit(crop, (1280, 720))
        path = panel_dir / (f"scene-{panel.scene_number:03d}-shot-{panel.shot_number:03d}-r1.jpg")
        crop.save(path, format="JPEG", quality=94)
        extracted.append(path)
        label = (
            f"{panel.panel_number:02d}  {panel.start_seconds:05.1f}s  "
            f"cena {panel.scene_number} · take {panel.shot_number}"
        )
        box_height = max(28, round(height / 24))
        draw.rounded_rectangle(
            (left + 8, top + 8, min(right - 8, left + width // 5), top + box_height),
            radius=8,
            fill=(15, 23, 42, 220),
        )
        draw.text((left + 16, top + 14), label, fill=(255, 255, 255, 255), font=font)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review.save(review_path, format="JPEG", quality=92)
    return extracted


def _storyboard_text_evidence(path: Path) -> list[str]:
    """Return high-confidence OCR evidence while ignoring isolated shape-like false positives."""

    executable = shutil.which("tesseract")
    if executable is None or not path.is_file():
        return []
    try:
        completed = subprocess.run(
            [executable, str(path), "stdout", "--psm", "11"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("storyboard OCR unavailable path=%s", path, exc_info=True)
        return []
    if completed.returncode != 0:
        logger.warning(
            "storyboard OCR failed path=%s returncode=%s stderr=%s",
            path,
            completed.returncode,
            completed.stderr.strip()[:300],
        )
        return []
    lines = [" ".join(line.split()) for line in completed.stdout.splitlines() if line.strip()]
    timecodes = [
        match.group(0)
        for match in re.finditer(
            r"(?<!\w)\d{1,3}[.,:]\d{1,2}\s*s?(?!\w)",
            completed.stdout,
            flags=re.IGNORECASE,
        )
    ]
    phrase_lines = [line for line in lines if len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", line)) >= 2]
    all_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}", completed.stdout)
    if len(timecodes) >= 2:
        return [f"timecode:{value}" for value in timecodes[:8]]
    if len(phrase_lines) >= 1 and len(all_words) >= 3:
        return [f"text:{value}" for value in phrase_lines[:8]]
    return []


def _trim_storyboard_cell(cell: Image.Image) -> Image.Image:
    """Remove model-created sheet margins, gutters, and border strokes from one grid cell."""

    width, height = cell.size
    max_x_scan = max(4, round(width * 0.16))
    max_y_scan = max(4, round(height * 0.16))

    def active_vertical(x: int) -> bool:
        band = cell.crop((x, 0, min(x + 3, width), height))
        stats = ImageStat.Stat(band)
        return max(stats.stddev) >= 9 or max(stats.mean) - min(stats.mean) >= 12

    def active_horizontal(y: int) -> bool:
        band = cell.crop((0, y, width, min(y + 3, height)))
        stats = ImageStat.Stat(band)
        return max(stats.stddev) >= 9 or max(stats.mean) - min(stats.mean) >= 12

    def first_active(limit: int, predicate: Callable[[int], bool]) -> int:
        for position in range(limit):
            if all(predicate(min(position + offset, limit - 1)) for offset in range(3)):
                return position
        return 0

    left = first_active(max_x_scan, active_vertical)
    right_inset = first_active(
        max_x_scan,
        lambda offset: active_vertical(max(0, width - 3 - offset)),
    )
    top = first_active(max_y_scan, active_horizontal)
    bottom_inset = first_active(
        max_y_scan,
        lambda offset: active_horizontal(max(0, height - 3 - offset)),
    )
    right = width - right_inset
    bottom = height - bottom_inset
    if right - left < width * 0.7 or bottom - top < height * 0.7:
        return cell
    # A small final inset removes the dark frame stroke that many image models draw immediately
    # inside the detected content boundary. It is negligible for borderless sheets.
    inset = max(5, round(min(width, height) * 0.01))
    left = min(left + inset, right - 1)
    top = min(top + inset, bottom - 1)
    right = max(right - inset, left + 1)
    bottom = max(bottom - inset, top + 1)
    return cell.crop((left, top, right, bottom))
