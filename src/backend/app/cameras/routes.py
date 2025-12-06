from datetime import datetime

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, jsonify, Response
from bson import ObjectId
from pymongo import ASCENDING

from src.backend.app.utils import to_doc, normalize_camera_payload, check_port_open
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
    if request.method == "POST":
        payload = normalize_camera_payload(request.form, cfg["DEFAULT_HTTP_PORT"])  # type: ignore[index]
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
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        flash("Camera not found", "warning")
        return redirect(url_for("cameras.list_cameras"))
    if request.method == "POST":
        payload = normalize_camera_payload(request.form, cfg["DEFAULT_HTTP_PORT"])  # type: ignore[index]
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
    db.cameras.delete_one({"_id": ObjectId(id)})
    flash("Camera deleted", "info")
    return redirect(url_for("cameras.list_cameras"))


@bp.post("/cameras/<id>/check")
def check_camera(id):
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        return jsonify({"error": "not found"}), 404
    ok = check_port_open(doc.get("ip"), int(doc.get("http_port") or cfg["DEFAULT_HTTP_PORT"]), cfg["REQUEST_TIMEOUT"])  # type: ignore[index]
    db.cameras.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"last_checked": datetime.utcnow(), "last_status": "online" if ok else "offline"}},
    )
    return jsonify({"id": id, "online": ok})


@bp.get("/cameras/<id>")
def view_camera(id):
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        flash("Camera not found", "warning")
        return redirect(url_for("cameras.list_cameras"))
    cam = to_doc(doc)
    return render_template("view_camera.html", camera=cam, current_user=current_app.get_current_user())  # type: ignore[attr-defined]


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
    gen = mjpeg_generator(
        rtsp=rtsp,
        fps=float(cfg["STREAM_MAX_FPS"]),
        jpeg_quality=int(cfg["STREAM_JPEG_QUALITY"]),
        connect_timeout=float(cfg["STREAM_CONNECT_TIMEOUT"]),
        idle_reconnect=float(cfg["STREAM_IDLE_RECONNECT"]),
        heartbeat_interval=float(cfg["STREAM_HEARTBEAT_INTERVAL"]),
    )
    return Response(gen, mimetype="multipart/x-mixed-replace; boundary=frame")


# Simple JSON API endpoints
@bp.get("/api/cameras")
def api_list():
    db = current_app.db  # type: ignore[attr-defined]
    cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
    return jsonify(cams)


@bp.post("/api/cameras")
def api_create():
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    payload = normalize_camera_payload(request.json or {}, cfg["DEFAULT_HTTP_PORT"])  # type: ignore[index]
    payload.update({
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    db.cameras.insert_one(payload)
    return jsonify({"status": "ok"}), 201


@bp.get("/api/cameras/<id>")
def api_get(id):
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        return jsonify({"error": "not found"}), 404
    return jsonify(to_doc(doc))


@bp.post("/api/cameras/<id>")
def api_update(id):
    cfg = current_app.config
    db = current_app.db  # type: ignore[attr-defined]
    payload = normalize_camera_payload(request.json or {}, cfg["DEFAULT_HTTP_PORT"])  # type: ignore[index]
    payload.update({"updated_at": datetime.utcnow()})
    db.cameras.update_one({"_id": ObjectId(id)}, {"$set": payload})
    return jsonify({"status": "ok"})


@bp.delete("/api/cameras/<id>")
def api_delete(id):
    db = current_app.db  # type: ignore[attr-defined]
    db.cameras.delete_one({"_id": ObjectId(id)})
    return jsonify({"status": "ok"})
