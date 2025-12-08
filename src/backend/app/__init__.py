import os
import atexit
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
        # Base directory for video recordings (mounted volume recommended)
        "RECORDINGS_DIR": os.getenv("RECORDINGS_DIR", "/data/recordings"),
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
        public_paths = {"/health", "/login", "/logout", "/signup", "/set-password"}
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
        user = app.get_current_user()  # type: ignore[attr-defined]
        if user is None:
            return redirect(url_for("auth.login", next=path))
        # Force password setup if flagged
        try:
            must_reset = bool(user.get("force_password_reset")) or not user.get("password_hash")
        except Exception:
            must_reset = False
        if must_reset and path not in {"/set-password", "/logout"}:
            return redirect(url_for("auth.set_password"))
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

    # Initialize background camera recorder/retention manager
    try:
        from src.backend.app.cameras.recorder import RecorderManager  # type: ignore
        recordings_dir = app.config.get("RECORDINGS_DIR") or "/data/recordings"
        app.recorder_manager = RecorderManager(app.db, recordings_dir)  # type: ignore[attr-defined]
        app.recorder_manager.start()  # type: ignore[attr-defined]
    except Exception as e:
        # Do not crash app if recording cannot be started; log to stdout
        try:
            print(f"[Recorder] Failed to start RecorderManager: {e}")
        except Exception:
            pass

    # IMPORTANT: Do NOT stop the recorder manager on each request teardown.
    # Flask may call teardown handlers after every request which would prematurely
    # stop background recording threads. Instead, stop them once the process
    # is exiting using atexit (portable across server runners).
    def _shutdown_recorders():
        mgr = getattr(app, "recorder_manager", None)
        if mgr is not None:
            try:
                print("[Recorder] Stopping RecorderManager (process exit)...")
                mgr.stop()
            except Exception:
                pass

    # Register process-exit shutdown
    atexit.register(_shutdown_recorders)

    return app
