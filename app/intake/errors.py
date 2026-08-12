"""Minimal application API errors with a stable response body."""


class AppApiError(Exception):
    """Raised for controlled API failures. Handled into a flat JSON body."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)  # Included in the error response and logs.
