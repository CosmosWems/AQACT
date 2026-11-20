"""Static description of the Puter.js AI layer.

Puter.js runs entirely in the browser and provides model access without API
keys, server configuration, or provider-specific setup.  The Flask process
never calls an AI provider and never stores credentials; it only describes the
routing/fallback policy and serves local dataset context to the browser.
"""

from __future__ import annotations

from assistant.quota import config as quota_config

AI_LAYER = "puter"

#: Task -> preferred model.  The browser router uses the same table.
ROUTING = {
    "statistics": "claude",
    "dataset": "deepseek-chat",
    "research": "claude",
    "chart": "gemini",
    "chat": "qwen",
    "fast": "qwen",
}

MODEL_LABELS = {
    "claude": "Claude",
    "gpt-4o": "GPT-4o",
    "gemini": "Gemini",
    "deepseek-chat": "DeepSeek",
    "qwen": "Qwen",
}

#: Ordered failover chain.  The preferred model is tried first, then the rest
#: of this chain in order.
FALLBACK_CHAIN = ["claude", "gpt-4o", "gemini", "deepseek-chat", "qwen"]

LIMITS = {
    "image_mb": 8.0,
    "file_mb": 8.0,
    "context_chars": 16000,
    "context_rows": 20,
    "history_turns": 8,
}


def public_status() -> dict:
    """Safe status payload for the UI and ``/api/health``.

    ``online`` describes the AI layer's intended state.  The browser performs
    the real availability check because Puter.js is loaded client-side.
    """
    return {
        "ok": True,
        "mode": "puter",
        "provider": "puter",
        "ai_layer": AI_LAYER,
        "online": True,
        "requires_key": False,
        "network_required": False,
        "client_network_required": True,
        "vision": True,
        "reason": "puter_ready",
        "routing": dict(ROUTING),
        "fallback": list(FALLBACK_CHAIN),
        "models": dict(MODEL_LABELS),
        "limits": dict(LIMITS),
        "quota": quota_config(),
    }
