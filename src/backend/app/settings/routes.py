from datetime import datetime

from flask import Blueprint, current_app, render_template, redirect, url_for, request, flash
from bson import ObjectId
from pymongo.errors import PyMongoError

bp = Blueprint("settings", __name__)


@bp.get("/settings")
def settings():
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    return render_template("settings.html", current_user=user)


@bp.post("/settings")
def update_settings():
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not user:
        return redirect(url_for("auth.login", next=url_for("settings.settings")))
    size = (request.form.get("dashboard_tile_size") or "").strip().lower()
    if size not in {"sm", "md", "lg"}:
        flash("Invalid dashboard tile size", "danger")
        return redirect(url_for("settings.settings"))
    try:
        current_app.db.users.update_one(  # type: ignore[attr-defined]
            {"_id": ObjectId(user["id"])},
            {"$set": {"settings.dashboard_tile_size": size, "updated_at": datetime.utcnow()}}
        )
        flash("Settings saved", "success")
    except PyMongoError as e:
        flash(f"Failed to save settings: {e}", "danger")
    return redirect(url_for("settings.settings"))
