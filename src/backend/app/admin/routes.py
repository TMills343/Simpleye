from datetime import datetime

from flask import Blueprint, current_app, request, redirect, url_for, flash, get_flashed_messages
from bson import ObjectId

from .utils import is_admin


bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/users")
def users():
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db = current_app.db  # type: ignore[attr-defined]
    items = list(db.users.find())
    def row(u):
        uid = str(u.get("_id"))
        roles_list = u.get("roles", []) or []
        if roles_list:
            roles = " ".join([f"<span class=\"badge text-bg-{ 'primary' if r=='admin' else 'secondary' } me-1\">{r.title()}</span>" for r in roles_list])
        else:
            roles = "<span class=\"badge text-bg-secondary\">Viewer</span>"
        email = u.get("email", "") or ""
        needs_reset = bool(u.get("force_password_reset")) or not u.get("password_hash")
        reset = f"<span class=\"badge text-bg-{'warning' if needs_reset else 'success'}\">{'Yes' if needs_reset else 'No'}</span>"
        return f"""
        <tr>
          <td>{u.get('username')}</td>
          <td>{roles}</td>
          <td>{('<a href=\'mailto:'+email+'\'>'+email+'</a>') if email else ''}</td>
          <td>{reset}</td>
          <td class=\"text-nowrap\">
            <a class=\"btn btn-sm btn-outline-secondary\" href=\"/admin/users/{uid}/edit\">Edit</a>
            <form class=\"d-inline\" method=\"post\" action=\"/admin/users/{uid}/reset_password\" onsubmit=\"return confirm('Reset password for this user?')\">
              <button class=\"btn btn-sm btn-outline-warning\" type=\"submit\">Reset Password</button>
            </form>
            <form class=\"d-inline\" method=\"post\" action=\"/admin/users/{uid}/delete\" onsubmit=\"return confirm('Delete this user?')\">
              <button class=\"btn btn-sm btn-outline-danger\" type=\"submit\">Delete</button>
            </form>
          </td>
        </tr>
        """
    rows = "".join(row(u) for u in items)

    # Common assets and header
    css = url_for('static', filename='css/style.css')
    favicon = url_for('static', filename='images/simpleye_favicon.png')
    logo = url_for('static', filename='images/simpleye_transparent_white_eye.png')
    username = (user or {}).get('username') or 'User'
    # Toasts for flashed messages
    msgs = get_flashed_messages(with_categories=True)
    toasts = ""
    if msgs:
        toast_items = []
        for category, message in msgs:
            bs = 'danger' if category == 'error' else (category if category in ['success','warning','info','primary','secondary','light','dark'] else 'info')
            toast_items.append(
                f"""
                <div class=\"toast align-items-center text-bg-{bs} border-0\" role=\"alert\" aria-live=\"assertive\" aria-atomic=\"true\" data-bs-delay=\"4000\">
                  <div class=\"d-flex\">
                    <div class=\"toast-body\">{message}</div>
                    <button type=\"button\" class=\"btn-close btn-close-white me-2 m-auto\" data-bs-dismiss=\"toast\" aria-label=\"Close\"></button>
                  </div>
                </div>
                """
            )
        toasts = f"""
        <div class=\"toast-container position-fixed top-0 end-0 p-3\">
            {''.join(toast_items)}
        </div>
        """

    nav_html = f"""
    <nav class=\"navbar navbar-expand-lg navbar-dark bg-dark\">
      <div class=\"container-fluid\">
        <a class=\"navbar-brand d-flex align-items-center\" href=\"/\"> 
          <img class=\"brand-logo me-2\" src=\"{logo}\" alt=\"Simpleye logo\"> 
          <span>Simpleye</span>
        </a>
        <button class=\"navbar-toggler\" type=\"button\" data-bs-toggle=\"collapse\" data-bs-target=\"#navbarsExample\" aria-controls=\"navbarsExample\" aria-expanded=\"false\" aria-label=\"Toggle navigation\"> 
          <span class=\"navbar-toggler-icon\"></span>
        </button>
        <div class=\"collapse navbar-collapse\" id=\"navbarsExample\"> 
          <ul class=\"navbar-nav me-auto mb-2 mb-lg-0\"> 
            <li class=\"nav-item\"><a class=\"nav-link\" href=\"/\">Dashboard</a></li>
            <li class=\"nav-item\"><a class=\"nav-link\" href=\"/cameras\">Cameras</a></li>
          </ul>
          <div class=\"d-flex align-items-center gap-2\">
              <div class=\"dropdown\"> 
                <button class=\"btn btn-outline-light dropdown-toggle\" type=\"button\" id=\"userMenu\" data-bs-toggle=\"dropdown\" aria-expanded=\"false\"> 
                  {username}
                </button> 
                <ul class=\"dropdown-menu dropdown-menu-end\" aria-labelledby=\"userMenu\"> 
                  <li><a class=\"dropdown-item active\" href=\"/admin/users\">User Management</a></li>
                  <li><hr class=\"dropdown-divider\"></li>
                  <li><a class=\"dropdown-item\" href=\"/settings\">Settings</a></li>
                  <li><hr class=\"dropdown-divider\"></li>
                  <li><a class=\"dropdown-item text-danger\" href=\"/logout\">Logout</a></li>
                </ul> 
              </div>
          </div>
        </div>
      </div>
    </nav>
    """

    html = f"""
    <!doctype html>
    <html lang=\"en\"> 
    <head>
      <meta charset=\"utf-8\">
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> 
      <title>User Management - Simpleye</title>
      <link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\"> 
      <link href=\"{css}\" rel=\"stylesheet\"> 
      <link rel=\"icon\" href=\"{favicon}\" type=\"image/png\"> 
    </head>
    <body>
      {nav_html}
      <div class=\"container py-4\"> 
        {toasts}
        <div class=\"d-flex justify-content-between align-items-center mb-3\">
          <h4 class=\"mb-0\">User Management</h4>
          <small class=\"text-muted\">Create, edit, and manage user roles</small>
        </div>
        <div class=\"row g-3\"> 
          <div class=\"col-12 col-xl-5\"> 
            <div class=\"card shadow-sm\"> 
              <div class=\"card-header d-flex align-items-center\"><span class=\"me-2\">👤</span><strong>Create User</strong></div> 
              <div class=\"card-body\"> 
                <form method=\"post\" action=\"/admin/users\"> 
                  <div class=\"mb-2\"> 
                    <label class=\"form-label\">Username</label> 
                    <input name=\"username\" class=\"form-control\" placeholder=\"jdoe\" required> 
                  </div> 
                  <div class=\"mb-2\"> 
                    <label class=\"form-label\">Email (optional)</label> 
                    <input name=\"email\" class=\"form-control\" placeholder=\"name@example.com\"> 
                  </div> 
                  <div class=\"mb-2\"> 
                    <label class=\"form-label\">Roles</label><br> 
                    <div class=\"form-check form-check-inline\"> 
                      <input class=\"form-check-input\" type=\"checkbox\" name=\"role_admin\" id=\"role_admin\"> 
                      <label class=\"form-check-label\" for=\"role_admin\">Admin</label> 
                    </div> 
                    <div class=\"form-check form-check-inline\"> 
                      <input class=\"form-check-input\" type=\"checkbox\" name=\"role_viewer\" id=\"role_viewer\" checked> 
                      <label class=\"form-check-label\" for=\"role_viewer\">Viewer</label> 
                    </div> 
                    <div class=\"form-text\">If no role is selected, the user defaults to Viewer.</div>
                  </div> 
                  <div class=\"alert alert-info py-2\">Password is not set by admin. The user will set it on first login.</div> 
                  <div class=\"mt-3 d-flex gap-2\"><button class=\"btn btn-primary\">Create</button> <a href=\"/\" class=\"btn btn-outline-secondary\">Back</a></div> 
                </form> 
              </div> 
            </div> 
          </div> 
          <div class=\"col-12 col-xl-7\"> 
            <div class=\"card shadow-sm\"> 
              <div class=\"card-header d-flex justify-content-between align-items-center\">
                <div><strong>Existing Users</strong></div>
              </div> 
              <div class=\"card-body p-0\"> 
                <div class=\"table-responsive\"> 
                  <table class=\"table table-striped mb-0 align-middle\"><thead><tr><th>Username</th><th>Roles</th><th>Email</th><th>Needs Password</th><th class=\"text-end\">Actions</th></tr></thead> 
                  <tbody>{rows}</tbody></table> 
                </div> 
              </div> 
            </div> 
          </div> 
        </div> 
      </div>
      <script src=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js\"></script>
      <script>document.querySelectorAll('.toast').forEach(t=>new bootstrap.Toast(t).show());</script>
    </body></html>
    """
    return html


@bp.post("/users")
def create_user():
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db = current_app.db  # type: ignore[attr-defined]
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip() or None
    if not username:
        flash("Username is required", "danger")
        return redirect(url_for("admin.users"))
    roles = []
    if request.form.get("role_admin"):
        roles.append("admin")
    if request.form.get("role_viewer") or not roles:
        roles.append("viewer")
    try:
        db.users.insert_one({
            "username": username,
            "email": email,
            "roles": roles,
            "password_hash": None,
            "force_password_reset": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })
        flash("User created", "success")
    except Exception as e:
        flash(f"Failed to create user: {e}", "danger")
    return redirect(url_for("admin.users"))


@bp.route("/users/<id>/edit", methods=["GET", "POST"])
def edit_user(id):
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.users.find_one({"_id": ObjectId(id)})
    if not doc:
        flash("User not found", "warning")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        roles = []
        if request.form.get("role_admin"):
            roles.append("admin")
        if request.form.get("role_viewer") or not roles:
            roles.append("viewer")
        try:
            db.users.update_one({"_id": ObjectId(id)}, {"$set": {
                "username": username or doc.get("username"),
                "email": email,
                "roles": roles,
                "updated_at": datetime.utcnow(),
            }})
            flash("User updated", "success")
            return redirect(url_for("admin.users"))
        except Exception as e:
            flash(f"Failed to update: {e}", "danger")
    # Render simple edit page
    is_admin_role = "admin" in (doc.get("roles") or [])
    is_viewer_role = "viewer" in (doc.get("roles") or []) or not (doc.get("roles") or [])
    email_val = doc.get("email", "") or ""
    css = url_for('static', filename='css/style.css')
    favicon = url_for('static', filename='images/simpleye_favicon.png')
    logo = url_for('static', filename='images/simpleye_transparent_white_eye.png')
    username = (user or {}).get('username') or 'User'
    msgs = get_flashed_messages(with_categories=True)
    toasts = ""
    if msgs:
        toast_items = []
        for category, message in msgs:
            bs = 'danger' if category == 'error' else (category if category in ['success','warning','info','primary','secondary','light','dark'] else 'info')
            toast_items.append(
                f"""
                <div class=\"toast align-items-center text-bg-{bs} border-0\" role=\"alert\" aria-live=\"assertive\" aria-atomic=\"true\" data-bs-delay=\"4000\"> 
                  <div class=\"d-flex\"> 
                    <div class=\"toast-body\">{message}</div> 
                    <button type=\"button\" class=\"btn-close btn-close-white me-2 m-auto\" data-bs-dismiss=\"toast\" aria-label=\"Close\"></button> 
                  </div> 
                </div>
                """
            )
        toasts = f"""
        <div class="toast-container position-fixed top-0 end-0 p-3">{''.join(toast_items)}</div>
        """

    nav_html = f"""
    <nav class=\"navbar navbar-expand-lg navbar-dark bg-dark\">
      <div class=\"container-fluid\">
        <a class=\"navbar-brand d-flex align-items-center\" href=\"/\"> 
          <img class=\"brand-logo me-2\" src=\"{logo}\" alt=\"Simpleye logo\"> 
          <span>Simpleye</span>
        </a>
        <button class=\"navbar-toggler\" type=\"button\" data-bs-toggle=\"collapse\" data-bs-target=\"#navbarsExample\" aria-controls=\"navbarsExample\" aria-expanded=\"false\" aria-label=\"Toggle navigation\"> 
          <span class=\"navbar-toggler-icon\"></span>
        </button>
        <div class=\"collapse navbar-collapse\" id=\"navbarsExample\"> 
          <ul class=\"navbar-nav me-auto mb-2 mb-lg-0\"> 
            <li class=\"nav-item\"><a class=\"nav-link\" href=\"/\">Dashboard</a></li>
            <li class=\"nav-item\"><a class=\"nav-link\" href=\"/cameras\">Cameras</a></li>
          </ul>
          <div class=\"d-flex align-items-center gap-2\">
              <div class=\"dropdown\"> 
                <button class=\"btn btn-outline-light dropdown-toggle\" type=\"button\" id=\"userMenu\" data-bs-toggle=\"dropdown\" aria-expanded=\"false\"> 
                  {username}
                </button> 
                <ul class=\"dropdown-menu dropdown-menu-end\" aria-labelledby=\"userMenu\"> 
                  <li><a class=\"dropdown-item active\" href=\"/admin/users\">User Management</a></li>
                  <li><hr class=\"dropdown-divider\"></li>
                  <li><a class=\"dropdown-item\" href=\"/settings\">Settings</a></li>
                  <li><hr class=\"dropdown-divider\"></li>
                  <li><a class=\"dropdown-item text-danger\" href=\"/logout\">Logout</a></li>
                </ul> 
              </div>
          </div>
        </div>
      </div>
    </nav>
    """

    html = f"""
    <!doctype html>
    <html lang=\"en\"> 
    <head>
      <meta charset=\"utf-8\">
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> 
      <title>Edit User - Simpleye</title>
      <link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\"> 
      <link href=\"{css}\" rel=\"stylesheet\"> 
      <link rel=\"icon\" href=\"{favicon}\" type=\"image/png\"> 
    </head>
    <body>
      {nav_html}
      <div class=\"container py-4\"> 
        {toasts}
        <div class=\"row justify-content-center\">
          <div class=\"col-12 col-lg-8 col-xl-6\">
            <div class=\"card shadow-sm\">
              <div class=\"card-header\"><strong>Edit User</strong></div>
              <div class=\"card-body\">
                <form method=\"post\"> 
                  <div class=\"mb-2\"> 
                    <label class=\"form-label\">Username</label>
                    <input name=\"username\" class=\"form-control\" value=\"{doc.get('username')}\" required>
                  </div>
                  <div class=\"mb-2\"> 
                    <label class=\"form-label\">Email</label>
                    <input name=\"email\" class=\"form-control\" value=\"{email_val}\"> 
                  </div>
                  <div class=\"mb-2\"> 
                    <label class=\"form-label\">Roles</label><br>
                    <div class=\"form-check form-check-inline\"> 
                      <input class=\"form-check-input\" type=\"checkbox\" name=\"role_admin\" id=\"role_admin\" {'checked' if is_admin_role else ''}>
                      <label class=\"form-check-label\" for=\"role_admin\">Admin</label>
                    </div>
                    <div class=\"form-check form-check-inline\"> 
                      <input class=\"form-check-input\" type=\"checkbox\" name=\"role_viewer\" id=\"role_viewer\" {'checked' if is_viewer_role else ''}>
                      <label class=\"form-check-label\" for=\"role_viewer\">Viewer</label>
                    </div>
                    <div class=\"form-text\">If neither role is checked, the user will default to Viewer.</div>
                  </div>
                  <div class=\"mt-3 d-flex gap-2\"><button class=\"btn btn-primary\">Save</button> <a href=\"/admin/users\" class=\"btn btn-outline-secondary\">Back</a></div>
                </form>
              </div>
            </div>
          </div>
        </div>
      </div>
      <script src=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js\"></script>
      <script>document.querySelectorAll('.toast').forEach(t=>new bootstrap.Toast(t).show());</script>
    </body></html>
    """
    return html


@bp.post("/users/<id>/delete")
def delete_user(id):
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db = current_app.db  # type: ignore[attr-defined]
    try:
        db.users.delete_one({"_id": ObjectId(id)})
        flash("User deleted", "info")
    except Exception as e:
        flash(f"Failed to delete: {e}", "danger")
    return redirect(url_for("admin.users"))


@bp.post("/users/<id>/reset_password")
def reset_password(id):
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db = current_app.db  # type: ignore[attr-defined]
    try:
        db.users.update_one({"_id": ObjectId(id)}, {"$set": {
            "password_hash": None,
            "force_password_reset": True,
            "updated_at": datetime.utcnow(),
        }})
        flash("Password reset. The user must set a new password on next login.", "warning")
    except Exception as e:
        flash(f"Failed to reset password: {e}", "danger")
    return redirect(url_for("admin.users"))
