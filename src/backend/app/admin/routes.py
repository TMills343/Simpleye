from flask import Blueprint, current_app

from .utils import is_admin


bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/users")
def users():
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db = current_app.db  # type: ignore[attr-defined]
    items = list(db.users.find())
    rows = "".join(
        f"<tr><td>{u.get('username')}</td><td>{', '.join(u.get('roles', []))}</td><td>{u.get('email','')}</td></tr>"
        for u in items
    )
    html = f"""
    <!doctype html>
    <html><head><title>User Management</title>
    <link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\"> 
    </head>
    <body class=\"container py-4\">\n
      <h3>User Management (placeholder)</h3>
      <p class=\"text-muted\">This is a temporary page. Future work: add create/edit/delete users, role management, password reset.</p>
      <table class=\"table table-sm table-striped\"><thead><tr><th>Username</th><th>Roles</th><th>Email</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <a class=\"btn btn-secondary btn-sm\" href=\"/\">Back</a>
    </body></html>
    """
    return html
