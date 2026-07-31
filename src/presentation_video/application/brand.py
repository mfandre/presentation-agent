from __future__ import annotations

from presentation_video.domain.models import (
    BrandKit,
    PresentationVisualPlan,
    VisualArtifact,
)


def apply_brand_kit(
    visual_plan: PresentationVisualPlan,
    brand: BrandKit | None,
) -> PresentationVisualPlan:
    if brand is None:
        return visual_plan
    palette = (
        f"brand palette primary {brand.primary_color}, secondary {brand.secondary_color}, "
        f"accent {brand.accent_color}, background {brand.background_color}"
    )
    scenes = [
        scene.model_copy(
            update={
                "visual_style": (
                    f"{scene.visual_style}. Brand direction: {brand.visual_style}; {palette}. "
                    "Apply these colors through lighting, materials, wardrobe, backgrounds, and "
                    "graphic accents where plausible; do not add a logo or invent visible text."
                )
            }
        )
        for scene in visual_plan.scenes
    ]
    direction = visual_plan.creative_direction.model_copy(
        update={
            "palette": [
                brand.primary_color,
                brand.secondary_color,
                brand.accent_color,
                brand.background_color,
            ],
            "accent_color": brand.accent_color,
        }
    )
    return visual_plan.model_copy(
        update={"creative_direction": direction, "scenes": scenes}
    )


def apply_brand_images(
    images: list[VisualArtifact],
    brand: BrandKit | None,
) -> list[VisualArtifact]:
    """Brand opening/closing cards are independent of narrative review assets."""
    return images


def apply_closing_image(
    images: list[VisualArtifact],
    brand: BrandKit | None,
) -> list[VisualArtifact]:
    """Compatibility wrapper: closing cards are appended after assembly, not to narrative scenes."""
    return images
