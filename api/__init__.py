"""HTTP blueprints for AQAT.

Importing this package does not create a Flask application; :func:`register`
attaches every feature blueprint to an app created by ``app.create_app``.
"""

from __future__ import annotations

from flask import Flask

from api.assistant_api import bp as assistant_bp
from api.export_api import bp as export_bp


def register(app: Flask) -> None:
    app.register_blueprint(assistant_bp)
    app.register_blueprint(export_bp)


__all__ = ["register", "assistant_bp", "export_bp"]
