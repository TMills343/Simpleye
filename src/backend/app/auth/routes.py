from datetime import datetime

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, session
from pymongo.errors import PyMongoError

bp = Blueprint("auth", __name__)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    db = current_app.db  # type: ignore[attr-defined]
    try:
        user_count = db.users.estimated_document_count()
    except Exception:
        user_count = 0
    # Only allow signup if no users exist (first boot)
    if user_count > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        email = (request.form.get("email") or "").strip() or None
        if not username or not password:
            flash("Username and password are required", "danger")
            return render_template("signup.html")
        try:
            db.users.insert_one({
                "username": username,
                "email": email,
                "password_hash": current_app.generate_password_hash(password),  # type: ignore[attr-defined]
                "roles": ["admin"],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            })
            flash("Admin account created. Please log in.", "success")
            return redirect(url_for("auth.login"))
        except PyMongoError as e:
            flash(f"Failed to create user: {e}", "danger")
    return render_template("signup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    db = current_app.db  # type: ignore[attr-defined]
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = request.args.get("next") or url_for("core.dashboard")
        doc = db.users.find_one({"username": username})
        if not doc or not current_app.check_password_hash(doc.get("password_hash", ""), password):  # type: ignore[attr-defined]
            flash("Invalid username or password", "danger")
            return render_template("login.html")
        # success
        session["user_id"] = str(doc["_id"])  # type: ignore[index]
        session["username"] = doc.get("username")
        return redirect(next_url)
    return render_template("login.html")


@bp.get("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("auth.login"))
