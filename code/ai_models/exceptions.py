"""Exception hierarchy for the ai_models package.

Every error raised on purpose derives from AiModelsError, so callers (the CLI and
the HTTP service) can catch one type and report a clean message instead of a
traceback.
"""


class AiModelsError(Exception):
    """Base class for all errors raised by ai_models."""


class DataError(AiModelsError):
    """Market data is missing, malformed or fails validation."""


class DataNotFoundError(DataError):
    """There is no data for the requested symbol."""


class InsufficientDataError(DataError):
    """There are too few rows to compute features, train or predict."""


class ModelNotFoundError(AiModelsError):
    """No trained model exists for the requested symbol or version."""


class ArtifactError(AiModelsError):
    """A stored model artifact is corrupt, tampered with or unreadable."""
