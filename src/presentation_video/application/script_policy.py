from __future__ import annotations

import math
import re

from presentation_video.domain.models import PresentationScript


def word_count(script: PresentationScript) -> int:
    return sum(len(scene.narration.split()) for scene in script.scenes)


def compact_script(
    script: PresentationScript,
    target_seconds: int,
    words_per_minute: int,
) -> PresentationScript:
    """Shorten narration deterministically while preserving complete sentences when possible."""

    maximum_words = math.floor(target_seconds * words_per_minute / 60)
    current_counts = [max(len(scene.narration.split()), 1) for scene in script.scenes]
    total = sum(current_counts)
    raw_budgets = [maximum_words * count / total for count in current_counts]
    budgets = [max(1, math.floor(value)) for value in raw_budgets]
    while sum(budgets) > maximum_words:
        largest = max(range(len(budgets)), key=budgets.__getitem__)
        budgets[largest] -= 1
    for index in sorted(
        range(len(budgets)),
        key=lambda item: raw_budgets[item] - math.floor(raw_budgets[item]),
        reverse=True,
    ):
        if sum(budgets) >= maximum_words:
            break
        budgets[index] += 1

    scenes = []
    for scene, budget in zip(script.scenes, budgets, strict=True):
        words = scene.narration.split()
        if len(words) <= budget:
            narration = scene.narration
        else:
            sentences = re.split(r"(?<=[.!?])\s+", scene.narration.strip())
            selected: list[str] = []
            used = 0
            for sentence in sentences:
                sentence_words = sentence.split()
                if used + len(sentence_words) > budget:
                    break
                selected.append(sentence)
                used += len(sentence_words)
            narration = (
                " ".join(selected)
                if selected
                else " ".join(words[:budget]).rstrip(".,;:") + "."
            )
        scenes.append(scene.model_copy(update={"narration": narration}))

    return retime_script(
        script.model_copy(update={"scenes": scenes}),
        target_seconds,
    )


def retime_script(
    script: PresentationScript,
    target_seconds: int,
) -> PresentationScript:
    """Distribute the requested duration proportionally to each scene's narration."""

    weights = [max(len(scene.narration.split()), 1) for scene in script.scenes]
    durations = [max(1, round(target_seconds * weight / sum(weights))) for weight in weights]
    durations[-1] += target_seconds - sum(durations)
    return script.model_copy(
        update={
            "scenes": [
                scene.model_copy(update={"target_seconds": duration})
                for scene, duration in zip(script.scenes, durations, strict=True)
            ],
            "total_estimated_seconds": target_seconds,
        }
    )
