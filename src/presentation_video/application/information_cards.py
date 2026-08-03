from __future__ import annotations

from pathlib import Path
from typing import cast

from PIL import Image, ImageColor, ImageDraw, ImageFont

from presentation_video.domain.models import (
    BrandKit,
    CriticalInformationKind,
    CriticalInformationUnit,
    ProductionMode,
    VisualArtifact,
)

_WIDTH = 1920
_HEIGHT = 1080
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_BOLD_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _BOLD_FONT_CANDIDATES if bold else _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _color(value: str, fallback: str) -> tuple[int, int, int]:
    try:
        resolved = ImageColor.getrgb(value)
    except ValueError:
        resolved = ImageColor.getrgb(fallback)
    return cast(tuple[int, int, int], resolved[:3])


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Font, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join((*current, word))
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _approval_rows(units: list[CriticalInformationUnit]) -> tuple[list[list[str]], list[str]]:
    rows: list[list[str]] = []
    notes: list[str] = []
    for unit in units:
        for fact in unit.facts:
            if " — " in fact:
                left, right = fact.split(" — ", 1)
                rows.append([left, right])
            else:
                notes.append(fact)
    return rows, notes


def _fitted_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    maximum_width: int,
    *,
    initial_size: int,
    minimum_size: int,
) -> Font:
    size = initial_size
    font = _font(size, bold=True)
    while size > minimum_size and draw.textlength(text, font=font) > maximum_width:
        size -= 2
        font = _font(size, bold=True)
    return font


def render_information_card(
    units: list[CriticalInformationUnit],
    output_path: Path,
    *,
    scene_number: int,
    shot_number: int,
    production_mode: ProductionMode,
    brand: BrandKit | None,
) -> VisualArtifact:
    if not units:
        raise ValueError("an exact information card requires at least one information unit")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    whiteboard = production_mode == ProductionMode.WHITEBOARD_EXPLAINER
    background = "#FFFFFF" if whiteboard else (brand.background_color if brand else "#F7F7FB")
    primary = "#111111" if whiteboard else (brand.primary_color if brand else "#183153")
    secondary = "#111111" if whiteboard else (brand.secondary_color if brand else "#237A70")
    accent = "#E5E5E5" if whiteboard else (brand.accent_color if brand else "#F2A900")
    ink = "#111111" if whiteboard else "#172033"
    muted = "#555555" if whiteboard else "#5F6878"

    image = Image.new("RGB", (_WIDTH, _HEIGHT), _color(background, "#F7F7FB"))
    draw = ImageDraw.Draw(image)
    subtitle_font = _font(28, bold=True)
    body_font = _font(32)
    body_bold = _font(32, bold=True)
    note_font = _font(29, bold=True)

    draw.rounded_rectangle((86, 70, 1834, 1010), radius=42, fill="#FFFFFF", outline=primary, width=4)
    draw.rounded_rectangle((86, 70, 1834, 198), radius=42, fill=primary)
    draw.rectangle((86, 152, 1834, 198), fill=primary)
    title = " · ".join(dict.fromkeys(unit.title for unit in units))
    title_font = _fitted_font(
        draw,
        title,
        1620,
        initial_size=64,
        minimum_size=38,
    )
    draw.text((142, 104), title, font=title_font, fill="#FFFFFF")

    approval_units = [
        unit for unit in units if unit.kind == CriticalInformationKind.APPROVAL_MATRIX
    ]
    remaining = [unit for unit in units if unit not in approval_units]
    y = 244
    if approval_units:
        rows, notes = _approval_rows(approval_units)
        left = 142
        right = 1778
        split = 925
        header_height = 70
        draw.rounded_rectangle((left, y, right, y + header_height), radius=18, fill=secondary)
        draw.text((left + 28, y + 18), "Faixa de valor", font=subtitle_font, fill="#FFFFFF")
        draw.text((split + 28, y + 18), "Aprovação mínima", font=subtitle_font, fill="#FFFFFF")
        y += header_height + 8
        row_height = (
            76
            if remaining
            else min(108, max(78, round(470 / max(len(rows), 1))))
        )
        for index, (value_range, approver) in enumerate(rows):
            fill = "#FFFFFF" if index % 2 == 0 else _color(background, "#F7F7FB")
            draw.rounded_rectangle((left, y, right, y + row_height), radius=12, fill=fill, outline="#D5D9E2", width=2)
            draw.line((split, y, split, y + row_height), fill="#D5D9E2", width=2)
            text_y = y + max((row_height - 38) // 2, 12)
            draw.text((left + 28, text_y), value_range, font=body_font, fill=ink)
            draw.text((split + 28, text_y), approver, font=body_bold, fill=ink)
            y += row_height + 8
        for note in notes:
            y += 10
            note_height = 66 if remaining else 92
            draw.rounded_rectangle((left, y, right, y + note_height), radius=18, fill=accent)
            note_lines = _wrap(draw, note, note_font, right - left - 64)
            draw.text((left + 30, y + 15), note_lines[0], font=note_font, fill=ink)
            y += note_height + 10

    if remaining:
        if approval_units:
            y += 12
        for unit in remaining:
            draw.text((142, y), unit.title, font=subtitle_font, fill=primary)
            y += 48
            for fact in unit.facts[:4]:
                lines = _wrap(draw, fact, body_font, 1540)
                draw.ellipse((150, y + 13, 166, y + 29), fill=accent)
                for line in lines:
                    draw.text((192, y), line, font=body_font, fill=ink)
                    y += 39
                y += 7

    page_numbers = sorted({number for unit in units for number in unit.source_slide_numbers})
    draw.text(
        (142, 954),
        f"Informação fiel à fonte · página{'s' if len(page_numbers) > 1 else ''} "
        + ", ".join(str(number) for number in page_numbers),
        font=_font(23),
        fill=muted,
    )
    image.save(output_path, format="PNG", optimize=True)
    return VisualArtifact(
        scene_number=scene_number,
        shot_number=shot_number,
        path=output_path,
        kind="image",
        locked_static=True,
    )
