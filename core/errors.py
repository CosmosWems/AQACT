"""Consistent API errors and Flask error registration."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from flask import Flask, jsonify, request

log = logging.getLogger("aqat.errors")


class ApiError(Exception):
    """An expected, user-facing error.

    ``hint`` is shown to the user as a concrete recovery step.  ``details`` is
    optional structured context for developers and is never a raw traceback.
    """

    def __init__(
        self,
        message: str,
        status: int = 400,
        *,
        code: str = "bad_request",
        hint: Optional[str] = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)
        self.code = code
        self.hint = hint
        self.details = details


def _payload(message: str, code: str, hint: Optional[str], request_id: str, details=None) -> dict:
    body = {
        "error": message,
        "code": code,
        "hint": hint,
        "request_id": request_id,
    }
    if details is not None:
        body["details"] = details
    return body


def _request_id() -> str:
    return getattr(request, "aqat_request_id", None) or uuid.uuid4().hex[:12]


def register_error_handlers(app: Flask) -> None:
    """Attach JSON-first error handlers to a Flask app."""

    def wants_json() -> bool:
        return request.path.startswith("/api/") or request.path == "/api"

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        if wants_json():
            return jsonify(_payload(exc.message, exc.code, exc.hint, _request_id(), exc.details)), exc.status
        return exc.message, exc.status

    @app.errorhandler(400)
    def _bad_request(exc):
        msg = "The request could not be understood."
        hint = "Check the submitted fields and try again."
        if wants_json():
            return jsonify(_payload(msg, "bad_request", hint, _request_id())), 400
        return msg, 400

    @app.errorhandler(404)
    def _not_found(exc):
        if request.path.startswith("/api/"):
            return jsonify(_payload(
                "The requested API endpoint or resource was not found.",
                "not_found",
                "Reload the page and retry. If this persists, the server may need restarting.",
                _request_id(),
            )), 404
        return "Not found", 404

    @app.errorhandler(405)
    def _method_not_allowed(exc):
        return jsonify(_payload(
            "This action is not allowed for the requested endpoint.",
            "method_not_allowed",
            "Reload the page to refresh the client.",
            _request_id(),
        )), 405

    @app.errorhandler(413)
    def _too_large(exc):
        return jsonify(_payload(
            "The upload is larger than the configured limit.",
            "payload_too_large",
            "Split the file into smaller parts or raise AQAT_MAX_UPLOAD_MB before starting the server.",
            _request_id(),
        )), 413

    @app.errorhandler(415)
    def _unsupported(exc):
        return jsonify(_payload(
            "Unsupported file or content type.",
            "unsupported_media_type",
            "Upload a CSV, XLSX, XLS or TXT file.",
            _request_id(),
        )), 415

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.path)
        if wants_json():
            return jsonify(_payload(
                "An unexpected error occurred while processing the request.",
                "internal_error",
                "Your data has not been lost. Try the operation again, or restart the app if the problem persists.",
                _request_id(),
            )), 500
        return "Internal server error", 500
