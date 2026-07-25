from __future__ import annotations

import re


_CONCEPT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Agentic AI",
        (
            "agentic ai",
            "agentic ia",
            "agentes de ia",
            "ai agents",
            "agentes autônomos",
            "orientar agentes",
            "orquestração de agentes",
        ),
    ),
    ("AI", ("inteligência artificial", "artificial intelligence", "assistentes de ia", " ia ")),
    ("Data Lakehouse", ("data lakehouse", "lakehouse")),
    ("data governance", ("governança de dados", "data governance", "dados governados")),
    ("data integration", ("dados integrados", "data integration", "pipelines")),
    ("human governance", ("decisões governadas por humanos", "human approval", "data owners")),
)


def infer_required_concepts(text: str) -> list[str]:
    normalized = f" {' '.join(text.casefold().split())} "
    concepts = [
        concept
        for concept, patterns in _CONCEPT_PATTERNS
        if any(pattern in normalized for pattern in patterns)
    ]
    if "Agentic AI" in concepts and "AI" in concepts:
        concepts.remove("AI")
    return concepts[:4]


def default_concept_visualization(concepts: list[str]) -> str:
    lowered = " ".join(concepts).casefold()
    parts: list[str] = []
    if "agentic ai" in lowered:
        parts.append(
            "Make agentic AI unmistakable as several distinct software task modules autonomously "
            "passing data and tool results through an orchestration flow, with a visible human "
            "approval checkpoint"
        )
    elif re.search(r"\bai\b", lowered):
        parts.append(
            "Make AI unmistakable as a software assistant analyzing an input and returning a "
            "structured result to a human operator"
        )
    if "lakehouse" in lowered or "integration" in lowered:
        parts.append(
            "show governed data from distinct sources converging into one organized data foundation"
        )
    if "governance" in lowered:
        parts.append("show an explicit human review or approval step in the workflow")
    return "; ".join(parts)
