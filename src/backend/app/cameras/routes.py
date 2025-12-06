from datetime import datetime

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, jsonify, Response
from bson import ObjectId
from pymongo import ASCENDING

from src.backend.app.utils.utils import to_doc, normalize_camera_payload, check_port_open
from src.backend.app.admin.utils import is_admin
from .utils import mjpeg_generator


bp = Blueprint("cameras", __name__)


@bp.get("/cameras")
def list_cameras():
    db = current_app.db  # type: ignore[attr-defined]
    cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
    return render_template("cameras.html", cameras=cams, current_user=current_app.get_current_user())  # type: ignore[attr-defined]


@bp.route("/cameras/new", methods=["GET", "POST"])
def add_camera():
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        if request.method == "POST":
            return ("Forbidden", 403)
        flash("Only admins can add cameras", "danger")
        return redirect(url_for("cameras.list_cameras"))
    if request.method == "POST":
        # DEFAULT_HTTP_PORT removed from ENV; use standard 80 as default
        payload = normalize_camera_payload(request.form, 80)
        if not payload["name"] or not payload["ip"]:
            flash("Name and IP are required", "danger")
            return render_template("camera_form.html", camera=payload, mode="new", current_user=current_app.get_current_user())  # type: ignore[attr-defined]
        payload.update({
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "last_checked": None,
            "last_status": None,
        })
        try:
            db.cameras.insert_one(payload)
            flash("Camera added", "success")
            return redirect(url_for("cameras.list_cameras"))
        except Exception as e:
            flash(f"Database error: {e}", "danger")
    return render_template("camera_form.html", camera={}, mode="new", current_user=current_app.get_current_user())  # type: ignore[attr-defined]


@bp.route("/cameras/<id>/edit", methods=["GET", "POST"])
def edit_camera(id):
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        if request.method == "POST":
            return ("Forbidden", 403)
        flash("Only admins can edit cameras", "danger")
        return redirect(url_for("cameras.list_cameras"))
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        flash("Camera not found", "warning")
        return redirect(url_for("cameras.list_cameras"))
    if request.method == "POST":
        # DEFAULT_HTTP_PORT removed from ENV; use standard 80 as default
        payload = normalize_camera_payload(request.form, 80)
        payload.update({"updated_at": datetime.utcnow()})
        try:
            db.cameras.update_one({"_id": ObjectId(id)}, {"$set": payload})
            flash("Camera updated", "success")
            return redirect(url_for("cameras.list_cameras"))
        except Exception as e:
            flash(f"Database error: {e}", "danger")
    return render_template("camera_form.html", camera=to_doc(doc), mode="edit", current_user=current_app.get_current_user())  # type: ignore[attr-defined]


@bp.post("/cameras/<id>/delete")
def delete_camera(id):
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return ("Forbidden", 403)
    db.cameras.delete_one({"_id": ObjectId(id)})
    flash("Camera deleted", "info")
    return redirect(url_for("cameras.list_cameras"))


@bp.post("/cameras/<id>/check")
def check_camera(id):
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        return jsonify({"error": "not found"}), 404
    # DEFAULT_HTTP_PORT and REQUEST_TIMEOUT removed; use in-code defaults
    ok = check_port_open(doc.get("ip"), int(doc.get("http_port") or 80), 2.0)
    db.cameras.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"last_checked": datetime.utcnow(), "last_status": "online" if ok else "offline"}},
    )
    return jsonify({"id": id, "online": ok})


@bp.get("/cameras/<id>/view")
def view_camera(id):
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        flash("Camera not found", "warning")
        return redirect(url_for("cameras.list_cameras"))
    cam = to_doc(doc)
    return render_template("view_camera.html", camera=cam, current_user=current_app.get_current_user())  # type: ignore[attr-defined]


@bp.get("/cameras/<id>")
def view_camera_redirect(id):
    # Backward compatibility: old canonical URL redirects to the new canonical /view path
    return redirect(url_for("cameras.view_camera", id=id), code=302)


@bp.get("/cameras/<id>/stream.mjpg")
def stream_mjpg(id):
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        return ("Not found", 404)
    rtsp = (doc.get("rtsp_url") or "").strip()
    if not rtsp:
        return ("RTSP URL not set", 400)
    # Use per-camera streaming settings if set; otherwise use built-in defaults
    def _float_or_default(val, default):
        try:
            if val in (None, ""):
                return float(default)
            f = float(val)
            return float(f)
        except (TypeError, ValueError):
            return float(default)
    def _int_or_default(val, default):
        try:
            if val in (None, ""):
                return int(default)
            return int(val)
        except (TypeError, ValueError):
            return int(default)
    # Defaults mirror old global ENV defaults
    fps_value = _float_or_default(doc.get("max_fps"), 10)
    jpeg_quality = _int_or_default(doc.get("jpeg_quality"), 70)
    connect_timeout = _float_or_default(doc.get("connect_timeout"), 10)
    idle_reconnect = _float_or_default(doc.get("idle_reconnect"), 10)
    heartbeat_interval = _float_or_default(doc.get("heartbeat_interval"), 2)
    gen = mjpeg_generator(
        rtsp=rtsp,
        fps=fps_value,
        jpeg_quality=jpeg_quality,
        connect_timeout=connect_timeout,
        idle_reconnect=idle_reconnect,
        heartbeat_interval=heartbeat_interval,
    )
    return Response(gen, mimetype="multipart/x-mixed-replace; boundary=frame")


# Simple JSON API endpoints
@bp.get("/api/cameras")
def api_list():
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
    # Do not expose RTSP URLs to non-admins
    if not is_admin(user):
        for c in cams:
            if "rtsp_url" in c:
                c["rtsp_url"] = None
    return jsonify(cams)


@bp.post("/api/cameras")
def api_create():
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    payload = normalize_camera_payload(request.json or {}, 80)
    payload.update({
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    db.cameras.insert_one(payload)
    return jsonify({"status": "ok"}), 201


@bp.get("/api/cameras/<id>")
def api_get(id):
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        return jsonify({"error": "not found"}), 404
    d = to_doc(doc)
    if not is_admin(user):
        d["rtsp_url"] = None
    return jsonify(d)


@bp.post("/api/cameras/<id>")
def api_update(id):
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    payload = normalize_camera_payload(request.json or {}, 80)
    payload.update({"updated_at": datetime.utcnow()})
    db.cameras.update_one({"_id": ObjectId(id)}, {"$set": payload})
    return jsonify({"status": "ok"})


@bp.delete("/api/cameras/<id>")
def api_delete(id):
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if not is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    db.cameras.delete_one({"_id": ObjectId(id)})
    return jsonify({"status": "ok"})
