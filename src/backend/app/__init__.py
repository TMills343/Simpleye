import os
from typing import Dict, Any

from flask import Flask, request, redirect, url_for, session
from dotenv import load_dotenv
from pymongo import MongoClient


def load_config() -> Dict[str, Any]:
    load_dotenv()
    return {
        "MONGO_URI": os.getenv("MONGO_URI", "mongodb://mongo:27017"),
        "MONGO_DB": os.getenv("MONGO_DB", "simpleye"),
        "FLASK_SECRET_KEY": os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex()),
        "APP_PORT": int(os.getenv("APP_PORT", "8000")),
        "DEFAULT_HTTP_PORT": int(os.getenv("DEFAULT_HTTP_PORT", "80")),
        "REQUEST_TIMEOUT": float(os.getenv("REQUEST_TIMEOUT", "2.0")),
        # Streaming settings
        "STREAM_MAX_FPS": float(os.getenv("STREAM_MAX_FPS", "10")),
        "STREAM_JPEG_QUALITY": int(os.getenv("STREAM_JPEG_QUALITY", "70")),
        "STREAM_CONNECT_TIMEOUT": float(os.getenv("STREAM_CONNECT_TIMEOUT", "10")),
        "STREAM_IDLE_RECONNECT": float(os.getenv("STREAM_IDLE_RECONNECT", "10")),
        "STREAM_HEARTBEAT_INTERVAL": float(os.getenv("STREAM_HEARTBEAT_INTERVAL", "2")),
    }


def create_mongo(cfg: Dict[str, Any]):
    client = MongoClient(cfg["MONGO_URI"], serverSelectionTimeoutMS=5000)
    db = client[cfg["MONGO_DB"]]
    # Ensure indexes lazily; ignore if fail
    try:
        from pymongo import ASCENDING
        db.cameras.create_index([("name", ASCENDING)], unique=False)
        db.cameras.create_index([("ip", ASCENDING)], unique=False)
        db.users.create_index([("username", ASCENDING)], unique=True)
        db.users.create_index([("email", ASCENDING)], unique=True, partialFilterExpression={"email": {"$type": "string"}})
    except Exception:
        pass
    return db


def _resolve_frontend_paths():
    # Prefer src/frontend if present; fallback to project-level templates/static
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    frontend_dir = os.path.join(base_dir, "frontend")
    tpl_dir = os.path.join(frontend_dir, "templates")
    static_dir = os.path.join(frontend_dir, "static")
    if not (os.path.isdir(tpl_dir) and os.path.isdir(static_dir)):
        # Next, prefer templates/static located directly under src/
        src_tpl = os.path.join(base_dir, "templates")
        src_static = os.path.join(base_dir, "static")
        if os.path.isdir(src_tpl) and os.path.isdir(src_static):
            tpl_dir, static_dir = src_tpl, src_static
        else:
            # Fallback to repo root existing structure (legacy layout)
            # base_dir points to .../src; the repository root is one level up
            repo_root = os.path.dirname(base_dir)
            tpl_dir = os.path.join(repo_root, "templates")
            static_dir = os.path.join(repo_root, "static")
    return tpl_dir, static_dir


def create_app():
    template_dir, static_dir = _resolve_frontend_paths()
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    cfg = load_config()
    app.config.update(cfg)
    app.secret_key = cfg["FLASK_SECRET_KEY"]

    # Attach DB for blueprints
    app.db = create_mongo(cfg)  # type: ignore[attr-defined]

    # Password helpers fallback
    try:
        from werkzeug.security import generate_password_hash, check_password_hash  # type: ignore
    except Exception:
        def generate_password_hash(p):
            return p
        def check_password_hash(h, p):
            return h == p
    app.generate_password_hash = generate_password_hash  # type: ignore[attr-defined]
    app.check_password_hash = check_password_hash  # type: ignore[attr-defined]

    # Simple current_user getter shared via app context
    from src.backend.app.utils.utils import get_current_user
    app.get_current_user = lambda: get_current_user(app.db, session)  # type: ignore[attr-defined]

    @app.before_request
    def enforce_auth():
        # allow certain paths public
        public_paths = {"/health", "/login", "/logout", "/signup"}
        path = request.path
        if path.startswith("/static/"):
            return None
        # First boot: if no users exist, force signup
        try:
            user_count = app.db.users.estimated_document_count()  # type: ignore[attr-defined]
        except Exception:
            user_count = 0
        if user_count == 0:
            if path != "/signup" and not path.startswith("/static/"):
                return redirect(url_for("auth.signup"))
            return None
        # If users exist, require login
        if path in public_paths:
            return None
        if app.get_current_user() is None:  # type: ignore[attr-defined]
            return redirect(url_for("auth.login", next=path))
        return None

    # Register blueprints
    from src.backend.app.core.routes import bp as core_bp
    from src.backend.app.auth.routes import bp as auth_bp
    from src.backend.app.settings import bp as settings_bp
    from src.backend.app.cameras import bp as cameras_bp
    from src.backend.app.admin.routes import bp as admin_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(admin_bp)

    # Legacy endpoint aliases to preserve existing templates that call url_for('login')
    try:
        app.add_url_rule(
            "/login",
            endpoint="login",
            view_func=app.view_functions["auth.login"],
            methods=["GET", "POST"],
        )
    except Exception:
        pass

    return app
