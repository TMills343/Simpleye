import os
import socket
from datetime import datetime
from typing import Dict, Any

from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from bson import ObjectId
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError
from dotenv import load_dotenv
import threading
import time

try:
    import cv2
except Exception:
    cv2 = None
try:
    import numpy as np
except Exception:
    np = None


def load_config() -> Dict[str, Any]:
    # Load .env if present
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
        # Reconnect/heartbeat to keep long-lived streams alive
        "STREAM_IDLE_RECONNECT": float(os.getenv("STREAM_IDLE_RECONNECT", "10")),
        "STREAM_HEARTBEAT_INTERVAL": float(os.getenv("STREAM_HEARTBEAT_INTERVAL", "2")),
    }


def create_mongo(cfg: Dict[str, Any]):
    client = MongoClient(cfg["MONGO_URI"], serverSelectionTimeoutMS=5000)
    db = client[cfg["MONGO_DB"]]
    # ensure index on name and ip for quick lookup
    db.cameras.create_index([("name", ASCENDING)], unique=False)
    db.cameras.create_index([("ip", ASCENDING)], unique=False)
    # users collection indexes
    try:
        db.users.create_index([("username", ASCENDING)], unique=True)
        # email optional and unique when present
        db.users.create_index([("email", ASCENDING)], unique=True, partialFilterExpression={"email": {"$type": "string"}})
    except Exception:
        pass
    return db


def check_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def normalize_camera_payload(data: Dict[str, Any], default_http_port: int) -> Dict[str, Any]:
    name = (data.get("name") or "").strip()
    ip = (data.get("ip") or "").strip()
    http_port = data.get("http_port") or data.get("port") or default_http_port
    try:
        http_port = int(http_port)
    except Exception:
        http_port = default_http_port
    rtsp_url = (data.get("rtsp_url") or "").strip()
    notes = (data.get("notes") or "").strip()
    enabled = str(data.get("enabled") or data.get("enabled", "on")).lower() in ["1", "true", "on", "yes"]
    return {
        "name": name,
        "ip": ip,
        "http_port": http_port,
        "rtsp_url": rtsp_url,
        "notes": notes,
        "enabled": enabled,
    }


def create_app():
    cfg = load_config()
    app = Flask(__name__)
    app.config.update(cfg)
    app.secret_key = cfg["FLASK_SECRET_KEY"]
    db = create_mongo(cfg)

    # Auth helpers
    try:
        from werkzeug.security import generate_password_hash, check_password_hash
    except Exception:
        def generate_password_hash(p):
            return p
        def check_password_hash(h, p):
            return h == p

    def get_current_user():
        uid = session.get("user_id")
        if not uid:
            return None
        try:
            doc = db.users.find_one({"_id": ObjectId(uid)})
            if not doc:
                return None
            user = dict(doc)
            user["id"] = str(user.pop("_id"))
            return user
        except Exception:
            return None

    @app.before_request
    def enforce_auth():
        # allow certain paths public
        public_paths = {"/health", "/login", "/logout", "/signup"}
        path = request.path
        if path.startswith("/static/"):
            return None
        # First boot: if no users exist, force signup
        try:
            user_count = db.users.estimated_document_count()
        except Exception:
            user_count = 0
        if user_count == 0:
            if path != "/signup" and not path.startswith("/static/"):
                return redirect(url_for("signup"))
            return None
        # If users exist, require login
        if path in public_paths:
            return None
        if get_current_user() is None:
            return redirect(url_for("login", next=path))
        return None

    def to_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
        # Convert Mongo doc for JSON/template
        d = dict(doc)
        d["id"] = str(d.pop("_id"))
        return d

    @app.route("/health")
    def health():
        try:
            # Ping MongoDB
            db.command("ping")
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "detail": str(e)}, 500

    @app.route("/")
    def index():
        # Dashboard view: show all enabled cameras that have RTSP URLs
        cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
        return render_template("dashboard.html", cameras=cams, current_user=get_current_user())

    @app.route("/dashboard")
    def dashboard():
        cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
        return render_template("dashboard.html", cameras=cams, current_user=get_current_user())

    @app.route("/cameras")
    def cameras():
        cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
        return render_template("cameras.html", cameras=cams, current_user=get_current_user())

    @app.route("/cameras/new", methods=["GET", "POST"])
    def add_camera():
        if request.method == "POST":
            payload = normalize_camera_payload(request.form, cfg["DEFAULT_HTTP_PORT"])
            if not payload["name"] or not payload["ip"]:
                flash("Name and IP are required", "danger")
                return render_template("camera_form.html", camera=payload, mode="new", current_user=get_current_user())
            payload.update({
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "last_checked": None,
                "last_status": None,
            })
            try:
                db.cameras.insert_one(payload)
                flash("Camera added", "success")
                return redirect(url_for("cameras"))
            except PyMongoError as e:
                flash(f"Database error: {e}", "danger")
        return render_template("camera_form.html", camera={}, mode="new", current_user=get_current_user())

    @app.route("/cameras/<id>/edit", methods=["GET", "POST"])
    def edit_camera(id):
        doc = db.cameras.find_one({"_id": ObjectId(id)})
        if not doc:
            flash("Camera not found", "warning")
            return redirect(url_for("cameras"))
        if request.method == "POST":
            payload = normalize_camera_payload(request.form, cfg["DEFAULT_HTTP_PORT"])
            payload.update({"updated_at": datetime.utcnow()})
            try:
                db.cameras.update_one({"_id": ObjectId(id)}, {"$set": payload})
                flash("Camera updated", "success")
                return redirect(url_for("cameras"))
            except PyMongoError as e:
                flash(f"Database error: {e}", "danger")
        return render_template("camera_form.html", camera=to_doc(doc), mode="edit", current_user=get_current_user())

    @app.post("/cameras/<id>/delete")
    def delete_camera(id):
        db.cameras.delete_one({"_id": ObjectId(id)})
        flash("Camera deleted", "info")
        return redirect(url_for("cameras"))

    @app.post("/cameras/<id>/check")
    def check_camera(id):
        doc = db.cameras.find_one({"_id": ObjectId(id)})
        if not doc:
            return jsonify({"error": "not found"}), 404
        ok = check_port_open(doc.get("ip"), int(doc.get("http_port") or cfg["DEFAULT_HTTP_PORT"]), cfg["REQUEST_TIMEOUT"])
        db.cameras.update_one(
            {"_id": ObjectId(id)},
            {"$set": {"last_checked": datetime.utcnow(), "last_status": "online" if ok else "offline"}},
        )
        return jsonify({"id": id, "online": ok})

    def _open_capture(rtsp: str, connect_timeout: float):
        if cv2 is None:
            raise RuntimeError("OpenCV is not available. Install opencv-python-headless.")
        # Some cameras require additional params; keep basic for now
        cap = cv2.VideoCapture(rtsp, cv2.CAP_FFMPEG)
        start = time.time()
        while not cap.isOpened():
            if time.time() - start > connect_timeout:
                cap.release()
                raise RuntimeError("Failed to open RTSP stream (timeout)")
            time.sleep(0.2)
        return cap

    def _mjpeg_generator(rtsp: str, fps: float, jpeg_quality: int, connect_timeout: float, idle_reconnect: float, heartbeat_interval: float):
        boundary = b"--frame"
        try:
            cap = _open_capture(rtsp, connect_timeout)
        except Exception as e:
            # yield a single error frame as text
            msg = f"RTSP error: {e}".encode()
            yield b"Content-Type: text/plain\r\n\r\n" + msg
            return

        delay = 1.0 / max(fps, 0.1)
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(max(1, min(100, jpeg_quality)))] if cv2 is not None else []
        last_frame_time = time.time()
        last_sent_time = 0.0
        last_jpg = None
        try:
            while True:
                ok, frame = cap.read()
                now = time.time()
                if ok and frame is not None:
                    # encode as JPEG
                    ok2, buf = cv2.imencode('.jpg', frame, encode_param)
                    if not ok2:
                        time.sleep(0.05)
                        continue
                    jpg = buf.tobytes()
                    last_jpg = jpg
                    last_frame_time = now
                    yield boundary + b"\r\n" + b"Content-Type: image/jpeg\r\n" + f"Content-Length: {len(jpg)}\r\n\r\n".encode() + jpg + b"\r\n"
                    last_sent_time = now
                    time.sleep(delay)
                    continue

                # No frame available
                # Heartbeat with last known good frame to keep connection alive
                if last_jpg is not None and (now - last_sent_time) >= max(0.2, heartbeat_interval):
                    jpg = last_jpg
                    yield boundary + b"\r\n" + b"Content-Type: image/jpeg\r\n" + f"Content-Length: {len(jpg)}\r\n\r\n".encode() + jpg + b"\r\n"
                    last_sent_time = now

                # If we've been idle too long, try to reconnect
                if (now - last_frame_time) >= max(1.0, idle_reconnect):
                    try:
                        cap.release()
                    except Exception:
                        pass
                    # attempt reopen
                    try:
                        cap = _open_capture(rtsp, connect_timeout)
                        last_frame_time = time.time()
                    except Exception as e:
                        # On failure, optionally emit a placeholder frame so clients don't go black
                        if last_jpg is None and cv2 is not None and np is not None:
                            try:
                                blank = np.zeros((240, 320, 3), dtype=np.uint8)
                                ok3, buf = cv2.imencode('.jpg', blank, encode_param)
                                if ok3:
                                    last_jpg = buf.tobytes()
                                    yield boundary + b"\r\n" + b"Content-Type: image/jpeg\r\n" + f"Content-Length: {len(last_jpg)}\r\n\r\n".encode() + last_jpg + b"\r\n"
                                    last_sent_time = time.time()
                            except Exception:
                                pass
                        # brief backoff before next attempt
                        time.sleep(0.5)
                        continue

                time.sleep(0.1)
        except GeneratorExit:
            pass
        finally:
            cap.release()

    @app.get("/cameras/<id>/stream.mjpg")
    def stream_camera(id):
        doc = db.cameras.find_one({"_id": ObjectId(id)})
        if not doc or not doc.get("rtsp_url"):
            return jsonify({"error": "not found or missing rtsp_url"}), 404
        if not doc.get("enabled", True):
            return jsonify({"error": "camera disabled"}), 400
        from flask import Response
        gen = _mjpeg_generator(
            doc["rtsp_url"],
            app.config["STREAM_MAX_FPS"],
            app.config["STREAM_JPEG_QUALITY"],
            app.config["STREAM_CONNECT_TIMEOUT"],
            app.config["STREAM_IDLE_RECONNECT"],
            app.config["STREAM_HEARTBEAT_INTERVAL"],
        )
        headers = {
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        return Response(gen, mimetype='multipart/x-mixed-replace; boundary=frame', headers=headers)

    @app.get("/cameras/<id>/view")
    def view_camera(id):
        doc = db.cameras.find_one({"_id": ObjectId(id)})
        if not doc:
            flash("Camera not found", "warning")
            return redirect(url_for("cameras"))
        return render_template("view_camera.html", camera=to_doc(doc), current_user=get_current_user())

    # JSON API
    @app.get("/api/cameras")
    def api_list():
        cams = [to_doc(c) for c in db.cameras.find().sort("name", ASCENDING)]
        return jsonify(cams)

    @app.post("/api/cameras")
    def api_create():
        payload = normalize_camera_payload(request.json or {}, cfg["DEFAULT_HTTP_PORT"])
        if not payload["name"] or not payload["ip"]:
            return jsonify({"error": "name and ip required"}), 400
        payload.update({
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "last_checked": None,
            "last_status": None,
        })
        res = db.cameras.insert_one(payload)
        doc = db.cameras.find_one({"_id": res.inserted_id})
        return jsonify(to_doc(doc)), 201

    @app.get("/api/cameras/<id>")
    def api_get(id):
        doc = db.cameras.find_one({"_id": ObjectId(id)})
        if not doc:
            return jsonify({"error": "not found"}), 404
        return jsonify(to_doc(doc))

    @app.patch("/api/cameras/<id>")
    def api_update(id):
        payload = normalize_camera_payload(request.json or {}, cfg["DEFAULT_HTTP_PORT"])
        payload.update({"updated_at": datetime.utcnow()})
        db.cameras.update_one({"_id": ObjectId(id)}, {"$set": payload})
        doc = db.cameras.find_one({"_id": ObjectId(id)})
        return jsonify(to_doc(doc))

    @app.delete("/api/cameras/<id>")
    def api_delete(id):
        db.cameras.delete_one({"_id": ObjectId(id)})
        return "", 204

    # Auth routes
    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        try:
            user_count = db.users.estimated_document_count()
        except Exception:
            user_count = 0
        # Only allow signup if no users exist (first boot)
        if user_count > 0:
            return redirect(url_for("login"))
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
                    "password_hash": generate_password_hash(password),
                    "roles": ["admin"],
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                })
                flash("Admin account created. Please log in.", "success")
                return redirect(url_for("login"))
            except PyMongoError as e:
                flash(f"Failed to create user: {e}", "danger")
        return render_template("signup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            next_url = request.args.get("next") or url_for("dashboard")
            doc = db.users.find_one({"username": username})
            if not doc or not check_password_hash(doc.get("password_hash", ""), password):
                flash("Invalid username or password", "danger")
                return render_template("login.html")
            # success
            session["user_id"] = str(doc["_id"])
            session["username"] = doc.get("username")
            return redirect(next_url)
        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        flash("Logged out", "info")
        return redirect(url_for("login"))

    @app.get("/settings")
    def settings():
        # Protected by before_request auth guard
        user = get_current_user()
        return render_template("settings.html", current_user=user)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=app.config["APP_PORT"], debug=True)
