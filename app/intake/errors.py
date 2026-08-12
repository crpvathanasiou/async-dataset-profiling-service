"""
Application-level API error type.

`AppApiError` is the single exception routers raise for controlled failures. It
carries the HTTP status plus the stable error code and message, and
`app/main.py` registers the handler that renders it as an `ApiErrorResponse`
body.

Boundary this enforces:
    service layer -> raises domain errors (e.g. IntakeServiceError)
    router        -> translates them into AppApiError (HTTP decision)
    main.py       -> serializes AppApiError into the documented JSON body

Because the status code is decided by the router and the body shape by the
handler, services stay free of HTTP concerns and clients get one predictable
error format instead of FastAPI's default `detail` payload.
"""


class AppApiError(Exception):
    """
    Controlled API failure with an explicit HTTP status and stable code.

    Inputs: `status_code` (the HTTP status the client should see), `code` (a
    machine-readable identifier, in practice a value from `IntakeErrorCode` or
    `ValidationErrorCode`), and `message` (human-readable text safe to return).

    Raising this instead of `HTTPException` keeps the response body under this
    application's control. Only information deliberately placed in `code` and
    `message` reaches the client; AWS/SDK detail stays in logs.
    """

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)  # Included in the error response and logs.
