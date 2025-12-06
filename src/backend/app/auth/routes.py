from datetime import datetime

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, session
from bson import ObjectId
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
        if not doc:
            flash("Invalid username or password", "danger")
            return render_template("login.html")
        # If no password has ever been set (new or reset), allow username-only entry and force set-password
        password_hash = doc.get("password_hash")
        must_reset = bool(doc.get("force_password_reset")) or not password_hash
        if not password_hash:
            # proceed to set-password without validating a password
            session["user_id"] = str(doc["_id"])  # type: ignore[index]
            session["username"] = doc.get("username")
            flash("Please set your password to continue.", "warning")
            return redirect(url_for("auth.set_password"))
        # If a password exists, validate it
        if not current_app.check_password_hash(password_hash or "", password):  # type: ignore[attr-defined]
            flash("Invalid username or password", "danger")
            return render_template("login.html")
        # success login
        session["user_id"] = str(doc["_id"])  # type: ignore[index]
        session["username"] = doc.get("username")
        if must_reset:
            flash("You must set a new password to continue.", "warning")
            return redirect(url_for("auth.set_password"))
        return redirect(next_url)
    return render_template("login.html")


@bp.get("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("auth.login"))


@bp.route("/set-password", methods=["GET", "POST"])
def set_password():
    # Require a logged-in user
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("auth.login", next=url_for("auth.set_password")))
    if request.method == "POST":
        pwd = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(pwd) < 6:
            flash("Password must be at least 6 characters.", "danger")
        elif pwd != confirm:
            flash("Passwords do not match.", "danger")
        else:
            # Update password and clear reset flag
            current_app.db.users.update_one(  # type: ignore[attr-defined]
                {"_id": ObjectId(uid)},
                {"$set": {"password_hash": current_app.generate_password_hash(pwd), "force_password_reset": False, "updated_at": datetime.utcnow()}},  # type: ignore[attr-defined]
            )
            flash("Password updated.", "success")
            return redirect(url_for("core.dashboard"))
    # Simple inline page
    html = """
    <!doctype html>
    <html><head><title>Set Password</title>
    <link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\"> 
    </head>
    <body class=\"container py-4\"> 
      <h3>Set your password</h3>
      <p class=\"text-muted\">Create a password for your account to continue.</p>
      <form method=\"post\"> 
        <div class=\"mb-2\">
          <label class=\"form-label\">New password</label>
          <input class=\"form-control\" type=\"password\" name=\"password\" required>
        </div>
        <div class=\"mb-2\">
          <label class=\"form-label\">Confirm password</label>
          <input class=\"form-control\" type=\"password\" name=\"confirm\" required>
        </div>
        <button class=\"btn btn-primary\">Save password</button>
      </form>
      <div class=\"mt-3\"><a class=\"btn btn-secondary btn-sm\" href=\"/logout\">Logout</a></div>
    </body></html>
    """
    return html
