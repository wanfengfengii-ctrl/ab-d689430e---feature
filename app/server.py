"""Flask application exposing POST /api/toolpaths/audit and GET /health."""

from __future__ import annotations

import os

from flask import Flask, jsonify, request

from .audit import audit


def create_app() -> Flask:
    app = Flask(__name__)
    # 5000 short G-code lines fit comfortably; reject oversized bodies.
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(
            error="invalid_request",
            reason="request body exceeds 2 MiB limit",
        ), 413

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/api/toolpaths/audit")
    def audit_toolpath():
        data = request.get_json(silent=True)
        if data is None:
            return jsonify(
                error="invalid_request",
                reason="request body must be valid JSON with a JSON object",
            ), 400
        ok, err = audit(data)
        if err is not None:
            status = 400 if err.get("error") == "invalid_request" else 422
            return jsonify(err), status
        return jsonify(ok), 200

    return app


app = create_app()


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
