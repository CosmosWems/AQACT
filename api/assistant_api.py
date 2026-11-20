"""Assistant API.

The browser owns the AI layer (Puter.js).  This blueprint only serves local,
deterministic work: compact dataset context, attachment reduction, chart-image
inspection, and the offline Q&A engine.  No provider credentials, proxy URLs,
or server-side model calls exist here.

Security model
--------------
* Same-origin requests only by default (``AQAT_ALLOWED_ORIGINS`` opts in).
* Attachment and image payloads are bounded before they are read.
* Attachments are parsed in memory and reduced; raw files are never persisted.
* The AI provider is never contacted by this process, so there is no key to
  leak, rotate, or misconfigure.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from typing import Optional

from flask import Blueprint, jsonify, request

from assistant import (
    answer_question,
    dataset_insights,
    describe_chart_image,
    describe_plot,
    explain_analysis,
    suggestions_for,
)
from assistant.context import (
    AttachmentError,
    build_attachment_context,
    build_dataset_context,
    render_context,
)
from assistant.profiling import dataset_profile
from assistant.quota import (
    QuotaError,
    config as quota_config,
    consume as consume_ai_request,
    mark_authenticated,
    mark_signed_out,
    status as quota_status,
)
from assistant.status import LIMITS, public_status
from core.cache import CACHE, cache_key
from core.errors import ApiError
from core.serialization import json_safe
from core.sessions import STORE

bp = Blueprint("assistant_api", __name__, url_prefix="/api/assistant")
log = logging.getLogger("aqat.assistant")

MAX_CONTEXT_CHARS = int(LIMITS["context_chars"])
MAX_CONTEXT_ROWS = int(LIMITS["context_rows"])
MAX_IMAGE_MB = float(LIMITS["image_mb"])
MAX_FILE_MB = float(LIMITS["file_mb"])
MAX_QUESTION_CHARS = 4000
MAX_HISTORY_CHARS = 8000


# ── Request guards ───────────────────────────────────────────────────────────
def _allowed_origins() -> set[str]:
    raw = os.environ.get("AQAT_ALLOWED_ORIGINS", "")
    return {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}


@bp.before_request
def _guard_assistant_request():
    """Reject cross-origin use and oversized assistant payloads early."""
    if request.method in {"POST", "PUT", "PATCH"}:
        max_bytes = int((MAX_IMAGE_MB + MAX_FILE_MB) * 1024 * 1024) + 1024 * 1024
        if (request.content_length or 0) > max_bytes:
            raise ApiError(
                "The assistant attachment is too large.",
                413,
                code="assistant_payload_too_large",
                hint=f"Keep images and data files under {max(MAX_IMAGE_MB, MAX_FILE_MB):g} MB.",
            )

    origin = (request.headers.get("Origin") or "").rstrip("/")
    if not origin:
        return
    if origin in _allowed_origins() or origin == request.host_url.rstrip("/"):
        return
    raise ApiError(
        "Cross-origin assistant requests are not allowed.",
        403,
        code="origin_not_allowed",
        hint="Open AQACT from its own address, or add the origin to AQAT_ALLOWED_ORIGINS.",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────
def _profile(session: dict, sid: str) -> dict:
    key = cache_key("profile", sid, session.get("version", 0), "assistant")
    return CACHE.get_or_set(key, lambda: dataset_profile(STORE.active_frame(sid)))


def _session_id(payload: Optional[dict] = None) -> str:
    payload = payload or {}
    return (
        payload.get("session_id")
        or request.args.get("session_id")
        or request.form.get("session_id")
        or ""
    )


def _local_meta(reason: str = "local_engine", *, model: str = "local-rules", **extra) -> dict:
    return {
        "used_cloud": False,
        "provider": "local",
        "model": model,
        "ai_layer": "local",
        "fallback_reason": reason,
        **extra,
    }


def _local_answer(question: str, frame, session: dict, profile: dict, mode) -> dict:
    return answer_question(question, frame, session=session, profile=profile, mode=mode)


def _context_text(frame, profile: dict, session: dict) -> str:
    context = build_dataset_context(
        frame,
        profile,
        session=session,
        max_rows=MAX_CONTEXT_ROWS,
    )
    return render_context(context, MAX_CONTEXT_CHARS)


def _answer_question(question: str, frame, session: dict, profile: dict, mode) -> dict:
    try:
        result = _local_answer(question, frame, session, profile, mode)
    except Exception:  # noqa: BLE001 - the assistant must never break the UI
        log.exception("Offline assistant failed.")
        result = {
            "answer": "I could not answer that from the current dataset. Try rephrasing the question.",
            "bullets": [],
            "caveats": [],
            "suggestions": [],
        }
    return {**result, **_local_meta()}


def _answer_image(raw_image: bytes) -> dict:
    description = describe_chart_image(raw_image)
    return {
        "answer": description.get("simple", ""),
        "bullets": description.get("observations", [])[:6],
        "caveats": description.get("caveats", []),
        "suggestions": description.get("suggested_questions", []),
        "intent": "image",
        "intent_label": "Chart image · local vision",
        "image": description.get("metrics"),
        **_local_meta("local_vision", model="local-vision"),
    }


def _read_image() -> tuple[Optional[str], Optional[bytes]]:
    upload = request.files.get("image") or request.files.get("file")
    if upload and upload.filename:
        return upload.filename, upload.read()
    body = request.get_json(silent=True) or {}
    data_url = body.get("image") or body.get("image_data_url")
    if not data_url:
        return None, None
    try:
        payload = str(data_url).split(",", 1)[-1]
        return "image", base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(
            "The image data could not be decoded.",
            400,
            code="bad_image_data",
            hint="Upload a PNG, JPEG, or WebP image under the configured limit.",
        ) from exc


def _read_attachment() -> tuple[Optional[str], Optional[bytes]]:
    upload = request.files.get("data_file") or request.files.get("file")
    if upload and upload.filename:
        return upload.filename, upload.read()
    body = request.get_json(silent=True) or {}
    text = body.get("attachment_text")
    if text:
        return str(body.get("attachment_name") or "attachment.txt"), str(text).encode("utf-8")
    return None, None


def _prepare_attachment(name: Optional[str], raw: Optional[bytes]) -> Optional[dict]:
    if not raw:
        return None
    try:
        return build_attachment_context(name or "attachment", raw)
    except AttachmentError as exc:
        raise ApiError(
            str(exc),
            400,
            code="attachment_error",
            hint="Attach a CSV, TSV, TXT, JSON, XLSX or XLS file under the size limit.",
        ) from exc


# ── Routes ───────────────────────────────────────────────────────────────────
@bp.get("/suggestions")
def suggestions():
    sid = _session_id()
    session = STORE.get(sid)
    if not session:
        return jsonify({
            "suggestions": [
                "What can you do?",
                "Summarise this dataset",
                "Explain this chart",
                "What is PM2.5?",
            ],
            "has_session": False,
        })
    frame = STORE.active_frame(sid)
    return jsonify({
        "suggestions": suggestions_for(frame, session),
        "has_session": True,
        "cleaned": session.get("cleaned") is not None,
        "last_analysis": (session.get("last_analysis") or {}).get("type"),
        "last_plot": (session.get("last_plot") or {}).get("plot_type"),
    })


@bp.route("/context", methods=["GET", "POST"])
def context():
    """Return the compact analytical pack used by the browser AI layer.

    This is local computation only.  The browser caches the response and sends
    it to Puter.js with the user's question.
    """
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    session = STORE.require(sid)
    frame = STORE.active_frame(sid)
    profile = _profile(session, sid)
    context_pack = build_dataset_context(
        frame,
        profile,
        session=session,
        max_rows=MAX_CONTEXT_ROWS,
    )
    return jsonify(json_safe({
        "ok": True,
        "session_id": sid,
        "version": int(session.get("version", 0)),
        "shape": [int(frame.shape[0]), int(frame.shape[1])],
        "context": context_pack,
        "context_text": render_context(context_pack, MAX_CONTEXT_CHARS),
        "profile": profile,
        "insights": dataset_insights(frame, profile=profile),
        "suggestions": suggestions_for(frame, session)[:6],
        "limits": dict(LIMITS),
    }))


@bp.post("/attachment")
def attachment():
    """Parse and reduce an attached data file without contacting a provider."""
    name, raw = _read_attachment()
    if not raw:
        raise ApiError(
            "No data file was provided.",
            400,
            code="missing_attachment",
            hint="Attach a CSV, TSV, TXT, JSON, XLSX or XLS file.",
        )
    result = _prepare_attachment(name, raw)
    return jsonify(json_safe({"ok": True, "attachment": result}))


@bp.post("/ask")
def ask():
    """Backwards-compatible JSON question endpoint (local engine only)."""
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    session = STORE.require(sid)
    question = str(body.get("question") or "").strip()
    if not question:
        raise ApiError(
            "Ask a question about the dataset.",
            400,
            code="empty_question",
            hint="Try: 'Summarise the dataset' or 'What is the average PM2.5?'.",
        )
    if len(question) > MAX_QUESTION_CHARS:
        raise ApiError(
            "The question is too long.",
            400,
            code="question_too_long",
            hint=f"Keep questions under {MAX_QUESTION_CHARS} characters.",
        )
    frame = STORE.active_frame(sid)
    profile = _profile(session, sid)
    answer = _answer_question(question, frame, session, profile, body.get("mode"))
    STORE.append_chat(sid, "user", question)
    STORE.append_chat(sid, "assistant", answer.get("answer", ""))
    return jsonify(json_safe({**answer, "question": question, "shape": list(frame.shape)}))


@bp.post("/chat")
def chat():
    """Unified local fallback: question + optional chart image + data file.

    The browser normally sends the question and compact context to Puter.js.
    If the online models are unavailable, it calls this endpoint so analytics,
    chart inspection, and the deterministic engine keep working offline.
    """
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    session = STORE.require(sid)
    question = str(body.get("question") or request.form.get("question") or "").strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise ApiError(
            "The question is too long.",
            400,
            code="question_too_long",
            hint=f"Keep questions under {MAX_QUESTION_CHARS} characters.",
        )

    mode = body.get("mode") or request.form.get("mode")
    frame = STORE.active_frame(sid)
    profile = _profile(session, sid)

    image_name, image_raw = _read_image()
    attachment_name, attachment_raw = _read_attachment()
    if not question and not image_raw and not attachment_raw:
        raise ApiError(
            "Ask a question, attach a chart image, or attach a data file.",
            400,
            code="empty_request",
            hint="Type a question or use the Chart image / Data file buttons.",
        )

    attachment = _prepare_attachment(attachment_name, attachment_raw)
    if image_raw:
        answer = _answer_image(image_raw)
        user_text = question or "Interpret the attached chart."
        markers = []
        if image_name:
            markers.append(f"[image] {image_name}")
        if attachment:
            markers.append(f"[data] {attachment['filename']}")
        if markers:
            user_text = f"{user_text} {' '.join(markers)}"
    else:
        answer = _answer_question(question, frame, session, profile, mode)
        user_text = question
        if attachment:
            user_text = f"{user_text} [data] {attachment['filename']}"

    STORE.append_chat(sid, "user", user_text)
    STORE.append_chat(sid, "assistant", answer.get("answer", ""))

    return jsonify(json_safe({
        **answer,
        "question": question,
        "shape": list(frame.shape),
        "attachment": (
            {key: attachment[key] for key in ("filename", "kind", "shape") if key in attachment}
            if attachment else None
        ),
        "image": (
            {key: answer["image"][key] for key in ("width", "height", "bytes") if key in answer["image"]}
            if answer.get("image") else None
        ),
    }))


@bp.route("/history", methods=["GET", "POST"])
def history():
    """Read or append session chat history.

    The browser appends Puter.js turns here so the conversation survives a
    reload even though the model call happens client-side.
    """
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        sid = _session_id(body)
        STORE.require(sid)
        role = "user" if str(body.get("role") or "").lower() == "user" else "assistant"
        text = str(body.get("text") or "").strip()
        if not text:
            raise ApiError("The history entry is empty.", 400, code="empty_history_entry")
        if len(text) > MAX_HISTORY_CHARS:
            text = text[:MAX_HISTORY_CHARS]
        STORE.append_chat(sid, role, text)
        return jsonify({"ok": True})

    sid = _session_id()
    session = STORE.get(sid)
    if not session:
        return jsonify({"messages": []})
    return jsonify({"messages": list(session.get("chat") or [])})


@bp.post("/explain")
def explain():
    """Deterministic explanation used for captions and analysis summaries.

    Publication captions stay local and reproducible.  Conversational chart
    explanation goes through the browser AI layer, which can read the chart
    image and the compact dataset context.
    """
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    session = STORE.require(sid)
    kind = str(body.get("kind") or "dataset").lower()
    mode = body.get("mode")
    frame = STORE.active_frame(sid)
    profile = _profile(session, sid)

    if kind == "plot":
        last_plot = session.get("last_plot") or {}
        plot_type = body.get("plot_type") or last_plot.get("plot_type") or "timeseries"
        params = body.get("params") or last_plot.get("params") or {}
        figure = body.get("figure")
        if not isinstance(figure, dict):
            figure = None
        result = describe_plot(plot_type, params=params, figure=figure, profile=profile,
                               column=params.get("column"))
    elif kind == "analysis":
        last = session.get("last_analysis") or {}
        analysis_type = body.get("analysis_type") or last.get("type")
        if not analysis_type:
            raise ApiError(
                "No analysis result is available to explain.",
                400,
                code="no_analysis",
                hint="Run an analysis first, then ask for an explanation.",
            )
        result = explain_analysis(
            analysis_type,
            body.get("result") or last.get("result") or {},
            params=body.get("params") or last.get("params") or {},
            column=body.get("column"),
        )
    else:
        items = dataset_insights(frame, profile=profile)
        result = {
            "kind": "dataset",
            "summary": items[0]["simple"] if items else "No insights are available yet.",
            "simple": " ".join(item["simple"] for item in items[:3]),
            "technical": " ".join(item["technical"] for item in items[:3]),
            "findings": [{"title": item["title"], "text": item["simple"]} for item in items],
            "caveats": ["Insights describe association and screening results, not causation."],
        }
    if mode in {"simple", "technical"}:
        result["selected"] = result.get(mode, result.get("summary"))
    return jsonify(json_safe(result))


@bp.post("/plot_image")
def plot_image():
    """Backwards-compatible local chart-image inspection endpoint."""
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    session = STORE.require(sid)
    question = str(body.get("question") or request.form.get("question") or "").strip()
    image_name, raw = _read_image()
    if not raw:
        raise ApiError(
            "No image was provided.",
            400,
            code="missing_image",
            hint="Attach a PNG or JPEG chart screenshot.",
        )
    frame = STORE.active_frame(sid)
    answer = _answer_image(raw)
    STORE.append_chat(sid, "user", f"[chart image] {image_name or 'chart'}")
    STORE.append_chat(sid, "assistant", answer.get("answer", ""))
    return jsonify(json_safe({
        **answer,
        "question": question,
        "filename": image_name or "chart",
        "size_bytes": len(raw),
        "shape": list(frame.shape),
        "suggestions": answer.get("suggestions", []),
    }))


@bp.get("/quota")
def quota_state():
    """Public AI usage counters and configurable limits for the assistant UI."""
    sid = _session_id()
    return jsonify(json_safe({
        "ok": True,
        "quota": quota_status(sid),
        "config": quota_config(),
    }))


@bp.post("/quota/session")
def quota_session_start():
    """Mark a browser Puter sign-in as the start of an AI session."""
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    STORE.require(sid)
    return jsonify(json_safe({"ok": True, "quota": mark_authenticated(sid)}))


@bp.post("/quota/release")
def quota_session_release():
    """End the server-side AI session without clearing rolling counters."""
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    STORE.require(sid)
    return jsonify(json_safe({"ok": True, "quota": mark_signed_out(sid)}))


@bp.post("/quota/signout")
def quota_threshold_signout():
    """Log a threshold-driven browser sign-out and end the AI session."""
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    reason = str(body.get("reason") or "quota_limit")[:64]
    if sid:
        mark_signed_out(sid)
    log.warning("AI threshold sign-out acknowledged by browser: reason=%s sid=%s",
                reason, sid[:8] if sid else "anonymous")
    return jsonify({"ok": True})


@bp.post("/quota/consume")
def quota_consume():
    """Authoritative pre-flight reservation for one browser-side AI request."""
    body = request.get_json(silent=True) or {}
    sid = _session_id(body)
    STORE.require(sid)
    if body.get("authenticated") is not True:
        return jsonify({
            "ok": False,
            "error": "Sign in to Puter before using AI features.",
            "code": "ai_auth_required",
            "status": 401,
        }), 401

    try:
        result = consume_ai_request(
            sid,
            model=str(body.get("model") or "")[:32],
            task=str(body.get("task") or "chat")[:32],
        )
    except QuotaError as exc:
        return jsonify(json_safe({
            "ok": False,
            "error": exc.message,
            "code": exc.code,
            **exc.payload,
        })), exc.status
    return jsonify(json_safe({"ok": True, **result}))


@bp.get("/health")
def assistant_health():
    """Describe the keyless Puter.js layer.  No credentials exist to expose."""
    return jsonify(public_status())
