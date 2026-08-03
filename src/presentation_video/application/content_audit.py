from __future__ import annotations

import re
from collections.abc import Iterable

from presentation_video.domain.models import (
    CriticalInformationKind,
    CriticalInformationUnit,
    PresentationDocument,
    PresentationScript,
    SceneScript,
    PresentationVisualPlan,
    MediaMode,
    VisualShotPlan,
)

_MONEY_RANGE = re.compile(
    r"^(?:até|de|acima de|more than|up to|from)\s+r?\$?\s*[\d.,]+(?:\s+a\s+r?\$?\s*[\d.,]+)?$",
    re.IGNORECASE,
)
_DEADLINE = re.compile(
    r"\b\d+\s+(?:dias?|days?|horas?|hours?)(?:\s+(?:úteis|corridos|business|calendar))?\b",
    re.IGNORECASE,
)
_CURRENCY = re.compile(r"(?:r\$|\$)\s*[\d.,]+", re.IGNORECASE)
_APPROVAL_TERMS = (
    "alçada",
    "approval matrix",
    "approval threshold",
    "aprovação mínima",
    "approval level",
)
_RULE_TERMS = ("proibido", "obrigatório", "vedado", "must not", "mandatory", "forbidden")


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _unique(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = " ".join(item.casefold().split())
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def _approval_matrix(page_number: int, text: str) -> CriticalInformationUnit | None:
    normalized = text.casefold()
    if not any(term in normalized for term in _APPROVAL_TERMS):
        return None
    lines = _lines(text)
    rows: list[str] = []
    for index, line in enumerate(lines[:-1]):
        if not _MONEY_RANGE.match(line):
            continue
        approver = lines[index + 1]
        if _MONEY_RANGE.match(approver) or len(approver) > 100:
            continue
        rows.append(f"{line} — {approver}")
    if len(rows) < 2:
        return None
    compact = " ".join(lines)
    prohibition_match = re.search(
        r"(?:o\s+)?fracion[^.]*\b(?:proibido|vedado|forbidden)\b[^.]*\.?",
        compact,
        re.IGNORECASE,
    )
    prohibition = prohibition_match.group(0).strip() if prohibition_match else ""
    facts = rows + ([prohibition] if prohibition else [])
    approvers = [row.split(" — ", 1)[1] for row in rows]
    return CriticalInformationUnit(
        id=f"page-{page_number}-approval-matrix",
        kind=CriticalInformationKind.APPROVAL_MATRIX,
        title="Limites e alçadas de aprovação",
        source_slide_numbers=[page_number],
        facts=facts,
        keywords=_unique(
            ["alçada", "aprovação", "valor", "fracionamento", *approvers]
        ),
        priority=5,
        exact_display_required=True,
        mandatory=True,
    )


def _deadline_unit(page_number: int, text: str) -> CriticalInformationUnit | None:
    lines = _lines(text)
    compact = " ".join(lines)
    facts: list[str] = []
    for match in _DEADLINE.finditer(compact):
        start = compact.rfind(".", 0, match.start()) + 1
        end = compact.find(".", match.end())
        end = len(compact) if end < 0 else end + 1
        context = compact[start:end].strip()
        facts.append(context[-220:])
    facts = _unique(facts)
    if not facts:
        return None
    return CriticalInformationUnit(
        id=f"page-{page_number}-deadlines",
        kind=CriticalInformationKind.DEADLINE,
        title="Prazos obrigatórios",
        source_slide_numbers=[page_number],
        facts=facts[:8],
        keywords=["prazo", "dias", "até", "deadline"],
        priority=4,
        exact_display_required=True,
        mandatory=True,
    )


def _exact_numbers_unit(page_number: int, text: str) -> CriticalInformationUnit | None:
    if any(term in text.casefold() for term in _APPROVAL_TERMS):
        return None
    lines = _lines(text)
    facts = _unique(line for line in lines if _CURRENCY.search(line))
    if len(facts) < 2:
        return None
    return CriticalInformationUnit(
        id=f"page-{page_number}-exact-values",
        kind=CriticalInformationKind.EXACT_NUMBERS,
        title="Valores de referência",
        source_slide_numbers=[page_number],
        facts=facts[:8],
        keywords=["valor", "limite", "faixa", "amount", "threshold"],
        priority=4,
        exact_display_required=True,
        mandatory=True,
    )


def audit_critical_information(
    document: PresentationDocument,
    enabled_signals: Iterable[str] | None = None,
) -> list[CriticalInformationUnit]:
    """Extract exact, omission-sensitive information before narrative compression."""

    enabled = {
        value.casefold()
        for value in (
            enabled_signals
            or ("approval_matrix", "deadlines", "exact_numbers", "tables")
        )
    }
    units: list[CriticalInformationUnit] = []
    for slide in document.slides:
        text = "\n".join((slide.title, slide.body_text, slide.speaker_notes))
        candidates = (
            _approval_matrix(slide.number, text)
            if "approval_matrix" in enabled
            else None,
            _deadline_unit(slide.number, text) if "deadlines" in enabled else None,
            _exact_numbers_unit(slide.number, text)
            if "exact_numbers" in enabled
            else None,
        )
        units.extend(candidate for candidate in candidates if candidate is not None)
    return units


def _unit_score(unit: CriticalInformationUnit, scene: SceneScript) -> int:
    text = " ".join(
        (
            scene.narration,
            scene.short_caption,
            scene.story_beat,
            scene.visual_intent,
            scene.scene_purpose,
        )
    ).casefold()
    keyword_score = sum(keyword.casefold() in text for keyword in unit.keywords)
    page_score = 20 * len(set(unit.source_slide_numbers) & set(scene.source_slide_numbers))
    return page_score + keyword_score


def _narration_mentions(unit: CriticalInformationUnit, narration: str) -> bool:
    normalized = narration.casefold()
    return any(keyword.casefold() in normalized for keyword in unit.keywords)


def _spoken_anchor(unit: CriticalInformationUnit) -> str:
    if unit.kind == CriticalInformationKind.APPROVAL_MATRIX:
        return (
            "As alçadas de aprovação variam conforme o valor total da solicitação; "
            "consulte no quadro cada faixa e o aprovador mínimo correspondente."
        )
    if unit.kind == CriticalInformationKind.DEADLINE:
        return "Observe também os prazos obrigatórios exibidos no quadro."
    return f"Observe os valores exatos apresentados no quadro de {unit.title.casefold()}."


def attach_critical_information(
    script: PresentationScript,
    units: list[CriticalInformationUnit],
) -> PresentationScript:
    """Map every mandatory unit to the most relevant scene without trusting page citation alone."""

    scenes = [scene.model_copy(deep=True) for scene in script.scenes]
    for unit in units:
        selected_index = max(
            range(len(scenes)),
            key=lambda index: (_unit_score(unit, scenes[index]), -index),
        )
        scene = scenes[selected_index]
        sources = sorted(set(scene.source_slide_numbers + unit.source_slide_numbers))
        information = [
            item for item in scene.critical_information if item.id != unit.id
        ] + [unit]
        narration = scene.narration
        if unit.mandatory and not _narration_mentions(unit, narration):
            narration = f"{narration.rstrip()} {_spoken_anchor(unit)}"
        scenes[selected_index] = scene.model_copy(
            update={
                "source_slide_numbers": sources,
                "critical_information": information,
                "narration": narration,
            }
        )
    return script.model_copy(update={"scenes": scenes})


def validate_critical_information_coverage(
    script: PresentationScript,
    units: list[CriticalInformationUnit],
) -> None:
    required = {unit.id for unit in units if unit.mandatory}
    assigned = {
        unit.id for scene in script.scenes for unit in scene.critical_information
    }
    missing = sorted(required - assigned)
    if missing:
        raise ValueError(f"critical information is not assigned to the narrative: {missing}")
    unspoken = sorted(
        unit.id
        for scene in script.scenes
        for unit in scene.critical_information
        if unit.mandatory and not _narration_mentions(unit, scene.narration)
    )
    if unspoken:
        raise ValueError(f"critical information lacks a spoken anchor: {unspoken}")


def validate_visual_critical_information_coverage(
    plan: PresentationVisualPlan,
    script: PresentationScript,
) -> None:
    required = {
        unit.id
        for scene in script.scenes
        for unit in scene.critical_information
        if unit.mandatory and unit.exact_display_required
    }
    covered: set[str] = set()
    for scene in plan.scenes:
        if scene.media_mode == MediaMode.STATIC:
            covered.update(unit.id for unit in scene.critical_information)
        for shot in scene.shots:
            if shot.locked_static and shot.media_mode == MediaMode.STATIC:
                covered.update(unit.id for unit in shot.critical_information)
    missing = sorted(required - covered)
    if missing:
        raise ValueError(
            "critical exact information has no deterministic static visual: "
            f"{missing}"
        )


def _information_score(unit: CriticalInformationUnit, excerpt: str) -> int:
    text = excerpt.casefold()
    return sum(keyword.casefold() in text for keyword in unit.keywords)


def assign_information_to_shots(
    shots: list[VisualShotPlan],
    units: list[CriticalInformationUnit],
    *,
    preserve_source_frame: bool,
) -> list[VisualShotPlan]:
    exact_units = sorted(
        (unit for unit in units if unit.exact_display_required),
        key=lambda unit: (-unit.priority, unit.id),
    )
    if not shots or not exact_units:
        return shots
    assigned: dict[int, list[CriticalInformationUnit]] = {}
    available = set(range(len(shots)))
    if len(shots) > 1:
        # Exact anchors must not consume an entire narrated scene. Reserve the take with the
        # weakest exact-information affinity for behavior/process motion.
        dynamic_index = min(
            available,
            key=lambda index: max(
                _information_score(unit, shots[index].narration_excerpt)
                for unit in exact_units
            ),
        )
        available.remove(dynamic_index)
    for unit in exact_units:
        candidates = available or set(assigned)
        selected = max(
            candidates,
            key=lambda index: (
                _information_score(unit, shots[index].narration_excerpt),
                -abs(index - len(shots) // 2),
                -index,
            ),
        )
        assigned.setdefault(selected, []).append(unit)
        available.discard(selected)
    result: list[VisualShotPlan] = []
    for index, shot in enumerate(shots):
        information = assigned.get(index, [])
        if not information:
            result.append(shot)
            continue
        source_number = information[0].source_slide_numbers[0]
        result.append(
            shot.model_copy(
                update={
                    "story_function": "exact_information_anchor",
                    "prompt": (
                        "Show the approved exact-information card for this narration interval. "
                        "Keep every supplied value, threshold, role, and deadline unchanged."
                    ),
                    "media_mode": MediaMode.STATIC,
                    "source_slide_number": (
                        source_number if preserve_source_frame else None
                    ),
                    "preserve_source_frame": preserve_source_frame,
                    "locked_static": True,
                    "critical_information": information,
                    "required_concepts": [unit.title for unit in information],
                }
            )
        )
    return result


def critical_information_summary(units: list[CriticalInformationUnit]) -> str:
    return "; ".join(
        f"{unit.id} ({unit.kind.value}, page {unit.source_slide_numbers[0]}): "
        + " | ".join(unit.facts)
        for unit in units
    )
