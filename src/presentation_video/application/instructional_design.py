from __future__ import annotations

import re

from presentation_video.domain.models import (
    InstructionalContentType,
    MediaMode,
    MotionPreset,
    PresentationScript,
    PresentationVisualPlan,
    TransitionPreset,
    VisualScenePlan,
)

_SYSTEM_DEMO_EVIDENCE = (
    "screenshot",
    "screen capture",
    "captura de tela",
    "source screen",
    "actual interface",
    "interface real",
    "tela do sistema",
    "tela do portal",
    "passo a passo no sistema",
    "campo de formulário",
    "form field",
)

_SYSTEM_CLICK_INSTRUCTIONS = (
    "clique",
    "click",
    "toque no botão",
    "tap the button",
    "selecione o menu",
    "select the menu",
    "preencha o campo",
    "fill in the field",
)

_PROCESS_ACTION_SIGNALS = (
    "processo",
    "process",
    "fluxo",
    "flow",
    "procedimento",
    "procedure",
    "etapa",
    "step",
    "registrar",
    "registre",
    "register",
    "enviar",
    "envio",
    "submit",
    "aprovar",
    "aprovação",
    "approve",
    "approval",
    "pagar",
    "pagamento",
    "payment",
    "digitalizar",
    "digitalizando",
    "scan",
)

_BEHAVIOR_ACTION_SIGNALS = (
    "comportamento",
    "behavior",
    "conduta",
    "conduct",
    "situação",
    "scenario",
    "exemplo",
    "example",
    "colaborador",
    "employee",
    "gestor",
    "manager",
)

_SIGNALS: tuple[tuple[InstructionalContentType, tuple[str, ...]], ...] = (
    (
        InstructionalContentType.RULE,
        (
            "regra",
            "rule",
            "política",
            "policy",
            "normativo",
            "obrigatório",
            "mandatory",
            "proibido",
            "prazo",
            "deadline",
            "limite",
            "limit",
            "valor",
            "amount",
        ),
    ),
    (
        InstructionalContentType.RECAP,
        (
            "resumo",
            "summary",
            "recap",
            "checklist",
            "principais pontos",
            "key takeaways",
            "lembre-se",
            "remember",
        ),
    ),
    (
        InstructionalContentType.PROCESS,
        (
            "processo",
            "process",
            "etapa",
            "step",
            "fluxo",
            "flow",
            "procedimento",
            "procedure",
            "como fazer",
            "how to",
            "sequência",
            "sequence",
        ),
    ),
    (
        InstructionalContentType.BEHAVIOR,
        (
            "comportamento",
            "behavior",
            "conduta",
            "conduct",
            "situação",
            "scenario",
            "exemplo",
            "example",
            "atendimento",
            "conversation",
            "colaborador",
            "employee",
        ),
    ),
)


def _normalized_scene_text(scene: VisualScenePlan, narration: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(
            (
                scene.scene_purpose,
                scene.story_beat,
                scene.prompt,
                scene.concept_visualization,
                narration,
            )
        ).lower(),
    )


def classify_instructional_scene(
    scene: VisualScenePlan,
    narration: str,
) -> InstructionalContentType:
    text = _normalized_scene_text(scene, narration)
    if any(signal in text for signal in _SYSTEM_DEMO_EVIDENCE) or any(
        instruction in text for instruction in _SYSTEM_CLICK_INSTRUCTIONS
    ):
        return InstructionalContentType.SYSTEM_DEMO
    if sum(signal in text for signal in _PROCESS_ACTION_SIGNALS) >= 2:
        return InstructionalContentType.PROCESS
    for content_type, signals in _SIGNALS:
        if any(signal in text for signal in signals):
            return content_type
    return InstructionalContentType.CONCEPT


def _dynamic_candidate(
    scene: VisualScenePlan,
    narration: str,
) -> tuple[InstructionalContentType, int]:
    text = _normalized_scene_text(scene, narration)
    process_score = sum(signal in text for signal in _PROCESS_ACTION_SIGNALS)
    behavior_score = sum(signal in text for signal in _BEHAVIOR_ACTION_SIGNALS)
    if process_score >= behavior_score and process_score:
        return InstructionalContentType.PROCESS, process_score
    if behavior_score:
        return InstructionalContentType.BEHAVIOR, behavior_score
    return InstructionalContentType.BEHAVIOR, 0


def _generated_training_still(
    scene: VisualScenePlan,
    content_type: InstructionalContentType,
    identity: str,
) -> VisualScenePlan:
    treatment = {
        InstructionalContentType.RULE: (
            "Create one elegant editorial illustration that represents the practical consequence "
            "of this rule through concrete objects, environment, scale, grouping, or human action. "
            "Do not reproduce the source page and do not show readable text."
        ),
        InstructionalContentType.RECAP: (
            "Create one cohesive editorial summary illustration that combines only the essential "
            "pictorial motifs already introduced in the training. Avoid lists, grids, labels, and "
            "readable text."
        ),
        InstructionalContentType.SYSTEM_DEMO: (
            "Create one polished instructional interface-inspired illustration that teaches the "
            "interaction represented by the source screen. Preserve the functional hierarchy and "
            "important visual relationships, but redesign the composition in the established "
            "training identity. Do not reproduce the original slide, screenshot, typography, or "
            "long text. Use simplified non-readable interface shapes and pictorial cues."
        ),
    }[content_type]
    return scene.model_copy(
        update={
            "instructional_type": content_type,
            "learning_objective": (
                scene.scene_purpose or scene.relationship_to_thesis or scene.story_beat
            ),
            "allow_readable_text": False,
            "media_mode": MediaMode.STATIC,
            "source_slide_number": None,
            "preserve_source_frame": False,
            "prompt": f"{treatment} {identity} Training scene request: {scene.prompt}",
            "visual_style": (
                "Polished corporate instructional editorial illustration, accessible composition, "
                f"consistent art direction, restrained detail. {identity}"
            ),
            "motion_preset": MotionPreset.NONE,
            "transition_preset": TransitionPreset.CUT,
            "shots": [],
            "visual_beats": [],
        }
    )


def _generated_training_scene(
    scene: VisualScenePlan,
    content_type: InstructionalContentType,
    identity: str,
) -> VisualScenePlan:
    treatment = {
        InstructionalContentType.CONCEPT: (
            "Create a clean editorial motion-graphics metaphor grounded in the source concept. "
            "Use simple concrete objects, restrained brand-aware color, strong hierarchy, and no "
            "readable text."
        ),
        InstructionalContentType.PROCESS: (
            "Demonstrate the process as a clear sequence of physical actions or an unlabeled visual "
            "flow. Reveal one step at a time without interfaces or readable text."
        ),
        InstructionalContentType.BEHAVIOR: (
            "Show one plausible workplace behavior scenario with anonymous adults, natural acting, "
            "and one observable correct or incorrect action. Avoid staged stock-photo poses."
        ),
        InstructionalContentType.SYSTEM_DEMO: (
            "Show a simplified non-readable system interaction only when an exact source screen "
            "cannot be preserved. Use no fake interface copy."
        ),
        InstructionalContentType.RULE: (
            "Represent the rule with one restrained, source-grounded visual example and no readable "
            "text."
        ),
        InstructionalContentType.RECAP: (
            "Create a concise visual recap using a small set of source-grounded pictorial elements "
            "and no readable text."
        ),
    }[content_type]
    return scene.model_copy(
        update={
            "instructional_type": content_type,
            "learning_objective": (
                scene.scene_purpose or scene.relationship_to_thesis or scene.story_beat
            ),
            "allow_readable_text": False,
            "media_mode": MediaMode.VIDEO,
            "source_slide_number": None,
            "preserve_source_frame": False,
            "prompt": f"{treatment} {identity} Training scene request: {scene.prompt}",
            "visual_style": (
                "Corporate instructional editorial design, clear visual hierarchy, accessible "
                "composition, realistic proportions, restrained motion, source-grounded. "
                f"{identity}"
            ),
            "camera_motion": "locked or very restrained instructional framing",
            "motion_preset": MotionPreset.PAN_RIGHT,
            "transition_preset": TransitionPreset.CUT,
            "shots": [],
            "visual_beats": [],
        }
    )


def direct_corporate_training_plan(
    plan: PresentationVisualPlan,
    script: PresentationScript,
) -> PresentationVisualPlan:
    scripts = {scene.scene_number: scene for scene in script.scenes}
    direction = plan.creative_direction
    palette = ", ".join(direction.palette)
    identity = (
        f"Maintain the production identity: visual motif '{direction.visual_motif}', "
        f"palette [{palette or 'source-derived'}], accent '{direction.accent_color or 'source-derived'}'. "
        "Reuse the same materials, line treatment, lighting logic, proportions, and composition "
        "language across all training scenes."
    )
    directed: list[VisualScenePlan] = []
    previous_was_static = False
    for scene in plan.scenes:
        narration = scripts[scene.scene_number].narration
        content_type = classify_instructional_scene(scene, narration)
        if content_type in {
            InstructionalContentType.RULE,
            InstructionalContentType.RECAP,
        }:
            directed_scene = (
                _generated_training_scene(scene, content_type, identity)
                if previous_was_static
                else _generated_training_still(scene, content_type, identity)
            )
            directed.append(directed_scene)
            previous_was_static = directed_scene.media_mode == MediaMode.STATIC
            continue
        if content_type == InstructionalContentType.SYSTEM_DEMO:
            directed.append(_generated_training_still(scene, content_type, identity))
            previous_was_static = True
            continue
        directed.append(_generated_training_scene(scene, content_type, identity))
        previous_was_static = False
    requires_dynamic_instruction = script.total_estimated_seconds > 30
    has_dynamic_instruction = any(
        scene.media_mode == MediaMode.VIDEO
        and scene.instructional_type
        in {InstructionalContentType.PROCESS, InstructionalContentType.BEHAVIOR}
        for scene in directed
    )
    if requires_dynamic_instruction and not has_dynamic_instruction:
        candidates = []
        for index, scene in enumerate(plan.scenes):
            candidate_type, score = _dynamic_candidate(
                scene,
                scripts[scene.scene_number].narration,
            )
            # Preserve a real system demonstration when another scene can carry motion.
            system_penalty = (
                1
                if directed[index].instructional_type == InstructionalContentType.SYSTEM_DEMO
                else 0
            )
            candidates.append((system_penalty, -score, -index, index, candidate_type))
        _, _, _, selected_index, selected_type = min(candidates)
        directed[selected_index] = _generated_training_scene(
            plan.scenes[selected_index],
            selected_type,
            identity,
        )
    return plan.model_copy(update={"scenes": directed})


def validate_corporate_training_plan(plan: PresentationVisualPlan) -> None:
    invalid = [
        scene.scene_number
        for scene in plan.scenes
        if scene.instructional_type is None
        or not scene.learning_objective
        or (
            scene.allow_readable_text
            and scene.media_mode != MediaMode.STATIC
        )
        or scene.preserve_source_frame
        or scene.source_slide_number is not None
    ]
    if invalid:
        raise ValueError(f"corporate training scenes violate instructional rules: {invalid}")
