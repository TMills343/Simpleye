from flask import Blueprint, current_app, render_template
from pymongo import ASCENDING

from src.backend.app.utils import to_doc  # shared utils


bp = Blueprint("core", __name__)


@bp.get("/health")
def health():
    try:
        current_app.db.command("ping")  # type: ignore[attr-defined]
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500


@bp.get("/")
def index():
    return dashboard()


@bp.get("/dashboard")
def dashboard():
    db = current_app.db  # type: ignore[attr-defined]
    cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    grid_size = "sm"
    try:
        pref = ((user or {}).get("settings") or {}).get("dashboard_tile_size")
        if pref in {"sm", "md", "lg"}:
            grid_size = pref
    except Exception:
        pass
    return render_template("dashboard.html", cameras=cams, current_user=user, grid_size=grid_size)
