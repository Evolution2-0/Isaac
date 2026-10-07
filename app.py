"""WSGI entry point for Isaac's Flask HTTP surface.

The cognitive kernel and existing aiohttp/WebSocket monitor remain owned by
isaac_core.py/monitor_server.py. This module exposes the existing Flask
blueprints through a conventional WSGI application for Flask-compatible hosts.
"""

from __future__ import annotations

import os

from flask import Flask, jsonify


def create_app() -> Flask:
    """Create the Flask WSGI application without starting the Isaac kernel."""
    app = Flask(__name__)

    from mcp_server import mcp_api

    app.register_blueprint(mcp_api)

    @app.get("/")
    def root():
        return jsonify(
            {
                "ok": True,
                "service": "isaac",
                "transport": "flask-wsgi",
                "mcp": "/api/mcp",
                "monitor": "/api/monitor/state",
                "health": "/healthz",
            }
        )

    @app.get("/api/monitor/state")
    def monitor_state():
        from monitor_api import state
        return state()

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "isaac"})

    return app


app = create_app()


def main() -> None:
    """Local development entry point; production should use WSGI."""
    app.run(
        host=os.getenv("ISAAC_BIND_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )


if __name__ == "__main__":
    main()
