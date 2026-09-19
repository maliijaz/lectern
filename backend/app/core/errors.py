"""Application exceptions and their HTTP mapping."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Plain integers rather than starlette's constants: the constant names have churned
# across versions and these never will.
HTTP_400_BAD_REQUEST = 400
HTTP_404_NOT_FOUND = 404
HTTP_415_UNSUPPORTED_MEDIA_TYPE = 415
HTTP_422_UNPROCESSABLE = 422
HTTP_501_NOT_IMPLEMENTED = 501
HTTP_502_BAD_GATEWAY = 502
HTTP_503_SERVICE_UNAVAILABLE = 503


class AppError(Exception):
    """Base for errors we raise deliberately and can show to a user."""

    status_code: int = HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFoundError(AppError):
    status_code = HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationFailed(AppError):
    status_code = HTTP_422_UNPROCESSABLE
    code = "validation_failed"


class UnsupportedFormat(AppError):
    status_code = HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_format"


class DependencyMissing(AppError):
    """An optional package or external tool is not installed.

    Carries an ``install`` hint so the UI can tell the teacher exactly what to run rather
    than showing an ImportError traceback.
    """

    status_code = HTTP_501_NOT_IMPLEMENTED
    code = "dependency_missing"

    def __init__(self, message: str, *, install: str = "") -> None:
        super().__init__(message, detail={"install": install} if install else {})
        self.install = install


class LLMError(AppError):
    status_code = HTTP_502_BAD_GATEWAY
    code = "llm_error"

    #: True when retrying the same request might succeed — a crashed model runner, a
    #: dropped connection, a rate limit. False for anything a retry cannot fix, such as a
    #: missing model or a bad API key.
    transient: bool = False

    def __init__(
        self, message: str, *, detail: dict | None = None, transient: bool = False
    ) -> None:
        super().__init__(message, detail=detail)
        self.transient = transient


class LLMUnavailable(LLMError):
    status_code = HTTP_503_SERVICE_UNAVAILABLE
    code = "llm_unavailable"


class StructuredOutputError(LLMError):
    """The model could not produce JSON matching the schema within the repair budget."""

    status_code = HTTP_502_BAD_GATEWAY
    code = "structured_output_failed"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, **exc.detail}},
        )
