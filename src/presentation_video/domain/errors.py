from __future__ import annotations


class UserFacingError(Exception):
    """Technical exception that also carries a safe message for the UI."""

    def __init__(self, technical_message: str, user_message: str) -> None:
        super().__init__(technical_message)
        self.user_message = user_message


class NarrativeDurationError(UserFacingError):
    """Raised when the source material cannot fit the requested video duration."""


class NarrativeGenerationError(UserFacingError):
    """Raised when an LLM cannot produce a structurally valid narrative."""


class MissingMediaDependencyError(UserFacingError):
    """Raised when a required local audio/video executable is unavailable."""
