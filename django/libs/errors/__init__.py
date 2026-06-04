"""Error envelope utilities.

Usage in service code:
    from libs.errors import AppError, NotFoundError, ValidationError
"""

from libs.errors.exceptions import (  # noqa: F401
    AppError,
    AuthError,
    ConflictError,
    ForbiddenError,
    InternalError,
    NotFoundError,
    RateLimitError,
    UpstreamError,
    ValidationError,
)
