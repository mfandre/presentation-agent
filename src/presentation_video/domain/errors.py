from __future__ import annotations


class UserFacingError(Exception):
    """Technical exception that also carries a safe message for the UI."""

    def __init__(self, technical_message: str, user_message: str) -> None:
        super().__init__(technical_message)
        self.user_message = user_message


class NarrativeDurationError(UserFacingError):
    """Raised when the source material cannot fit the requested video duration."""


class DurationReviewRequired(Exception):
    """Pauses a job before paid media generation so the user can choose its duration."""

    def __init__(self, requested_seconds: int, estimated_seconds: int, word_count: int) -> None:
        super().__init__(
            f"narrative needs approximately {estimated_seconds}s for {word_count} words; "
            f"requested duration is {requested_seconds}s"
        )
        self.requested_seconds = requested_seconds
        self.estimated_seconds = estimated_seconds
        self.word_count = word_count


class NarrativeGenerationError(UserFacingError):
    """Raised when an LLM cannot produce a structurally valid narrative."""


class MissingMediaDependencyError(UserFacingError):
    """Raised when a required local audio/video executable is unavailable."""
