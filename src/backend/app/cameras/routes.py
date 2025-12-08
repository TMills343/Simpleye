from datetime import datetime

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, jsonify, Response
from bson import ObjectId
from pymongo import ASCENDING

from src.backend.app.utils.utils import to_doc, normalize_camera_payload, check_port_open
from src.backend.app.admin.utils import is_admin
from .utils import mjpeg_generator
import os
from flask import send_from_directory
from datetime import timedelta
from math import ceil
import tempfile
import subprocess
import json
from werkzeug.exceptions import Forbidden


# -----------------------------
# JSON safety helpers
# -----------------------------
def _json_safe(obj):
    """Recursively convert Mongo/py types to JSON-serializable primitives.
    - ObjectId -> str
    - datetime -> ISO string with trailing 'Z'
    - dict/list/tuple -> recurse
    """
    try:
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            try:
                return obj.isoformat() + 'Z'
            except Exception:
                return str(obj)
        if isinstance(obj, dict):
            return {k: _json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            t = type(obj)
            return t(_json_safe(v) for v in obj)
        return obj
    except Exception:
        # As a last resort, stringify
        try:
            return str(obj)
        except Exception:
            return None


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


@bp.get("/cameras/<id>/review")
def review_camera(id):
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        flash("Camera not found", "warning")
        return redirect(url_for("cameras.list_cameras"))
    cam = to_doc(doc)
    return render_template("review_camera.html", camera=cam, current_user=current_app.get_current_user())  # type: ignore[attr-defined]


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


def _recordings_root():
    root = current_app.config.get("RECORDINGS_DIR") or "/data/recordings"
    return root


@bp.get("/api/cameras/<id>/recordings")
def api_list_recordings(id):
    """List recorded JPEG frames for a camera between optional start/end ISO times.

    Returns a JSON object with buckets grouped by minute for efficient timeline rendering.
    Each item contains minute path and list of files (seconds/millis) for that minute.
    """
    db = current_app.db  # type: ignore[attr-defined]
    doc = db.cameras.find_one({"_id": ObjectId(id)})
    if not doc:
        return jsonify({"error": "not found"}), 404
    # Parse time range (UTC). Format: YYYY-MM-DDTHH:MM (minute precision) or full ISO.
    start = request.args.get("start")
    end = request.args.get("end")
    # Build base dir
    base = os.path.join(_recordings_root(), str(ObjectId(id)))
    if not os.path.isdir(base):
        return jsonify({"minutes": []})
    # If no range provided, default to last 60 minutes
    now = datetime.utcnow()
    try:
        if start:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            start_dt = now - timedelta(minutes=60)
    except Exception:
        start_dt = now - timedelta(minutes=60)
    try:
        if end:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            end_dt = now
    except Exception:
        end_dt = now
    # Clamp
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt
    # Collect directories between range
    # We iterate possible years/months/days/hours/minutes within range by scanning filesystem.
    minutes = []
    # Iterate years present
    for y in sorted(os.listdir(base)):
        yp = os.path.join(base, y)
        if not os.path.isdir(yp):
            continue
        try:
            yi = int(y)
        except Exception:
            continue
        for m in sorted(os.listdir(yp)):
            mp = os.path.join(yp, m)
            if not os.path.isdir(mp):
                continue
            try:
                mi = int(m)
            except Exception:
                continue
            for d in sorted(os.listdir(mp)):
                dp = os.path.join(mp, d)
                if not os.path.isdir(dp):
                    continue
                try:
                    di = int(d)
                except Exception:
                    continue
                for h in sorted(os.listdir(dp)):
                    hp = os.path.join(dp, h)
                    if not os.path.isdir(hp):
                        continue
                    try:
                        hi = int(h)
                    except Exception:
                        continue
                    for mi2 in sorted(os.listdir(hp)):
                        mip = os.path.join(hp, mi2)
                        if not os.path.isdir(mip):
                            continue
                        try:
                            mii = int(mi2)
                            dt = datetime(yi, mi, di, hi, mii)
                        except Exception:
                            continue
                        if dt < start_dt or dt > end_dt:
                            continue
                        # List files in this minute
                        files = []
                        frames = []
                        is_hls = False
                        try:
                            names = sorted(os.listdir(mip))
                            # Detect HLS by presence of index.m3u8
                            if 'index.m3u8' in names:
                                is_hls = True
                                # Include playlist and segment files, and attempt to parse segment timing
                                playlist_path = os.path.join(mip, 'index.m3u8')
                                p_segments = []
                                pdt_start_iso = None
                                try:
                                    with open(playlist_path, 'r', encoding='utf-8', errors='ignore') as pf:
                                        last_dur = None
                                        cur_pdt = None
                                        cur_uri = None
                                        for line in pf:
                                            line = line.strip()
                                            if not line:
                                                continue
                                            if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
                                                try:
                                                    cur_pdt = line.split(':', 1)[1].strip()
                                                    if pdt_start_iso is None:
                                                        pdt_start_iso = cur_pdt
                                                except Exception:
                                                    cur_pdt = None
                                            elif line.startswith('#EXTINF:'):
                                                # duration may have comma at end
                                                try:
                                                    dur_str = line.split(':',1)[1].strip()
                                                    if dur_str.endswith(','):
                                                        dur_str = dur_str[:-1]
                                                    last_dur = float(dur_str)
                                                except Exception:
                                                    last_dur = None
                                            elif not line.startswith('#'):
                                                # This is a segment URI
                                                cur_uri = line
                                                if cur_uri.endswith('.ts'):
                                                    files.append(cur_uri)
                                                    p_segments.append({
                                                        "name": cur_uri,
                                                        "duration": last_dur,
                                                        "startIso": cur_pdt,
                                                    })
                                                    # reset duration (PDT may or may not repeat per seg)
                                                    last_dur = None
                                                    cur_pdt = None
                                except Exception:
                                    # If parsing fails, just list files below as fallback
                                    for fn in names:
                                        if fn.endswith('.m3u8') or fn.endswith('.ts'):
                                            files.append(fn)
                            else:
                                for fn in names:
                                    if not fn.endswith('.jpg'):
                                        continue
                                    files.append(fn)
                                    # Attempt to parse SS_ms from filename to compute precise timestamp
                                    try:
                                        base = os.path.splitext(fn)[0]
                                        parts = base.split('_')
                                        ss = int(parts[0]) if parts and parts[0].isdigit() else None
                                        ms = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                                        if ss is not None:
                                            fdt = datetime(yi, mi, di, hi, mii, ss, ms * 1000)
                                            frames.append({
                                                "name": fn,
                                                "ts": fdt.isoformat() + 'Z'
                                            })
                                    except Exception:
                                        # Ignore parsing errors; client can fall back to file order
                                        pass
                        except Exception:
                            files = []
                            frames = []
                        if files:
                            rel = os.path.join(y, m, d, h, mi2)
                            item = {
                                "minute": dt.isoformat() + 'Z',
                                "path": rel.replace('\\', '/'),
                                "count": len(files),
                                "files": files,
                                "frames": frames,
                            }
                            if is_hls:
                                item["kind"] = "hls"
                                # Attach parsed segments if available
                                if 'p_segments' in locals() and p_segments:
                                    item["segments"] = p_segments
                                if 'pdt_start_iso' in locals() and pdt_start_iso:
                                    item["pdtStartIso"] = pdt_start_iso
                            else:
                                item["kind"] = "jpeg"
                            minutes.append(item)
    return jsonify({"minutes": minutes})


@bp.get("/cameras/<id>/recordings/<path:relpath>")
def get_recording_file(id, relpath):
    """Serve recorded files for playback (JPEG frames or HLS playlists/segments)."""
    # Secure path under the camera directory only
    cam_dir = os.path.join(_recordings_root(), str(ObjectId(id)))
    # Normalize and ensure relpath does not escape cam_dir
    safe_root = os.path.abspath(cam_dir)
    target_dir = os.path.abspath(os.path.join(safe_root, os.path.dirname(relpath)))
    if not target_dir.startswith(safe_root):
        return ("Forbidden", 403)
    filename = os.path.basename(relpath)
    full = os.path.join(target_dir, filename)
    if not os.path.isfile(full):
        return ("Not found", 404)
    mimetype = None
    if filename.endswith('.m3u8'):
        mimetype = 'application/vnd.apple.mpegurl'
    elif filename.endswith('.ts'):
        mimetype = 'video/mp2t'
    elif filename.endswith('.jpg'):
        mimetype = 'image/jpeg'
    return send_from_directory(target_dir, filename, mimetype=mimetype)


# -----------------------------
# Clips helpers and endpoints
# -----------------------------

def _safe_clip_dir(cam_id: str, ts: datetime) -> str:
    base = _recordings_root()
    p = os.path.join(base, str(ObjectId(cam_id)), 'clips', ts.strftime('%Y'), ts.strftime('%m'), ts.strftime('%d'))
    os.makedirs(p, exist_ok=True)
    return p


def _sanitize_for_filename(s: str) -> str:
    return ''.join([c if c.isalnum() or c in ('-', '_') else '-' for c in s])


def _iter_hls_minutes_between(cam_dir: str, start_dt: datetime, end_dt: datetime):
    """Yield (minute_dt, abs_dir, rel_dir) for minutes in range that contain HLS playlists."""
    for y in sorted(os.listdir(cam_dir)):
        yp = os.path.join(cam_dir, y)
        if not os.path.isdir(yp):
            continue
        for m in sorted(os.listdir(yp)):
            mp = os.path.join(yp, m)
            if not os.path.isdir(mp):
                continue
            for d in sorted(os.listdir(mp)):
                dp = os.path.join(mp, d)
                if not os.path.isdir(dp):
                    continue
                for h in sorted(os.listdir(dp)):
                    hp = os.path.join(dp, h)
                    if not os.path.isdir(hp):
                        continue
                    for mi2 in sorted(os.listdir(hp)):
                        mip = os.path.join(hp, mi2)
                        if not os.path.isdir(mip):
                            continue
                        try:
                            dt = datetime(int(y), int(m), int(d), int(h), int(mi2))
                        except Exception:
                            continue
                        if dt < start_dt or dt > end_dt:
                            continue
                        if os.path.isfile(os.path.join(mip, 'index.m3u8')):
                            rel = os.path.join(y, m, d, h, mi2).replace('\\','/')
                            yield dt, mip, rel


def _collect_segments_for_window(cam_id: str, start_dt: datetime, end_dt: datetime):
    """Parse minute playlists and return list of segments with absolute start times and durations
    that overlap [start_dt, end_dt). Returns list of dicts with keys: path, start_ms, dur_s.
    """
    cam_root = os.path.join(_recordings_root(), str(ObjectId(cam_id)))
    segs = []
    for mdt, mip, rel in _iter_hls_minutes_between(cam_root, start_dt - timedelta(minutes=1), end_dt + timedelta(minutes=1)):
        pl = os.path.join(mip, 'index.m3u8')
        try:
            with open(pl, 'r', encoding='utf-8', errors='ignore') as pf:
                cur_pdt = None
                last_dur = None
                for raw in pf:
                    line = raw.strip()
                    if not line:
                        continue
                    if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
                        try:
                            cur_pdt = line.split(':',1)[1].strip()
                        except Exception:
                            cur_pdt = None
                    elif line.startswith('#EXTINF:'):
                        try:
                            dv = line.split(':',1)[1].strip()
                            if dv.endswith(','):
                                dv = dv[:-1]
                            last_dur = float(dv)
                        except Exception:
                            last_dur = None
                    elif not line.startswith('#'):
                        # segment uri
                        if not line.endswith('.ts'):
                            continue
                        start_ms = None
                        if cur_pdt:
                            try:
                                start_ms = int(datetime.fromisoformat(cur_pdt.replace('Z','+00:00')).timestamp()*1000)
                            except Exception:
                                start_ms = None
                        dur_s = last_dur or 0.0
                        abs_path = os.path.join(mip, line)
                        if start_ms is not None:
                            segs.append({
                                'path': abs_path,
                                'start_ms': start_ms,
                                'dur_s': dur_s,
                            })
                        # reset
                        last_dur = None
                        cur_pdt = None
        except Exception:
            continue
    # Filter overlap with window
    if not segs:
        return []
    segs.sort(key=lambda s: s['start_ms'])
    win_start = int(start_dt.timestamp()*1000)
    win_end = int(end_dt.timestamp()*1000)
    out = []
    for s in segs:
        s_start = s['start_ms']
        s_end = s_start + int((s['dur_s'] or 0)*1000)
        if s_end <= win_start or s_start >= win_end:
            continue
        out.append(s)
    return out


@bp.get('/api/cameras/<id>/clips')
def api_list_clips(id):
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    limit = int(request.args.get('limit', 50))
    before = request.args.get('before')
    q = {'camera_id': str(ObjectId(id))}
    if before:
        try:
            bdt = datetime.fromisoformat(before.replace('Z','+00:00'))
            q['created_at'] = {'$lt': bdt}
        except Exception:
            pass
    try:
        cur = db.clips.find(q).sort('created_at', -1).limit(limit)
    except Exception:
        return jsonify({'clips': []})
    items = []
    # Resolve requester identity for permissions
    uid = ''
    try:
        uid = str(user.get('id')) if isinstance(user, dict) else ''
    except Exception:
        uid = ''
    admin = is_admin(user)
    for doc in cur:
        d = _json_safe(dict(doc))
        d['id'] = str(doc.get('_id')) if doc.get('_id') is not None else d.get('id')
        # Build download URL from relative path
        rel = d.get('path_rel')
        if rel:
            # Allow either 'clips/...' or plain relative under clips dir
            rel_clean = rel[6:] if rel.startswith('clips/') else rel
            d['download_url'] = f"/cameras/{id}/clips/{rel_clean}"
            d['stream_url'] = f"/cameras/{id}/clips/stream/{rel_clean}"
        # Permission: admin or creator can delete
        creator = str(doc.get('created_by') or '')
        d['can_delete'] = bool(admin or (creator and creator == uid))
        items.append(d)
    return jsonify({'clips': items})


@bp.post('/api/cameras/<id>/clips')
def api_create_clip(id):
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if user is None:
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    start = body.get('start')
    end = body.get('end')
    name_raw = (body.get('name') or '').strip()
    if not start or not end:
        return jsonify({'error': 'start and end are required'}), 400
    try:
        start_dt = datetime.fromisoformat(start.replace('Z','+00:00')).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(end.replace('Z','+00:00')).replace(tzinfo=None)
    except Exception:
        return jsonify({'error': 'invalid timestamps'}), 400
    if end_dt <= start_dt:
        return jsonify({'error': 'end must be after start'}), 400
    # Find if camera is HLS mode
    cam = db.cameras.find_one({'_id': ObjectId(id)})
    if not cam:
        return jsonify({'error': 'camera not found'}), 404
    if (cam.get('recording_mode') or 'hls').lower() != 'hls':
        return jsonify({'error': 'clips are supported only for HLS recordings at the moment'}), 400
    # Limit maximum duration to protect resources (e.g., 30 minutes)
    max_minutes = 30
    if end_dt - start_dt > timedelta(minutes=max_minutes):
        end_dt = start_dt + timedelta(minutes=max_minutes)
    # Collect overlapping segments
    segs = _collect_segments_for_window(id, start_dt, end_dt)
    if not segs:
        return jsonify({'error': 'no segments found in the requested range'}), 404
    # Compute offsets
    win_start_ms = int(start_dt.timestamp()*1000)
    first = segs[0]
    offset_in_first = max(0.0, (win_start_ms - first['start_ms']) / 1000.0)
    total_duration = max(0.1, (end_dt.timestamp() - start_dt.timestamp()))
    # Prepare concat list file
    try:
        with tempfile.TemporaryDirectory() as td:
            list_path = os.path.join(td, 'list.txt')
            with open(list_path, 'w', encoding='utf-8') as lf:
                for s in segs:
                    lf.write(f"file '{s['path'].replace("'","'\\''")}'\n")
            # Output path
            clip_dir = _safe_clip_dir(id, start_dt)
            default_base = f"{start_dt.isoformat(timespec='seconds')}__{end_dt.isoformat(timespec='seconds')}"
            base_name = _sanitize_for_filename(name_raw) if name_raw else _sanitize_for_filename(default_base)
            # Ensure .mp4 extension
            fname = base_name if base_name.lower().endswith('.mp4') else (base_name + '.mp4')
            out_path = os.path.join(clip_dir, fname)
            # Run ffmpeg: concat -> trim via ss/t
            # We re-encode for precise cut boundaries
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat', '-safe', '0', '-i', list_path,
                '-ss', f"{offset_in_first:.3f}", '-t', f"{total_duration:.3f}",
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                '-an', out_path
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0 or not os.path.isfile(out_path):
                return jsonify({'error': 'ffmpeg failed', 'stderr': proc.stderr[-1000:]}), 500
            # Save clip doc
            # Store path relative to camera root (may include leading 'clips/') for backward compatibility
            rel = os.path.relpath(out_path, os.path.join(_recordings_root(), str(ObjectId(id)))).replace('\\','/')
            size_bytes = os.path.getsize(out_path)
            # Normalize creator id to string to avoid ObjectId in API/DB
            try:
                created_by_val = user.get('id') if isinstance(user, dict) else None
                if isinstance(created_by_val, ObjectId):
                    created_by_val = str(created_by_val)
            except Exception:
                created_by_val = None

            doc = {
                'camera_id': str(ObjectId(id)),
                'start_iso': start_dt.isoformat() + 'Z',
                'end_iso': end_dt.isoformat() + 'Z',
                'duration_s': total_duration,
                'path_rel': rel,
                'size_bytes': size_bytes,
                'created_at': datetime.utcnow(),
                'created_by': created_by_val,
                'name': (name_raw or None),
            }
            ins = db.clips.insert_one(doc)
            # Build JSON-safe response
            resp = dict(doc)
            resp['id'] = str(ins.inserted_id)
            rel_clean = rel[6:] if rel.startswith('clips/') else rel
            resp['download_url'] = f"/cameras/{id}/clips/{rel_clean}"
            resp['stream_url'] = f"/cameras/{id}/clips/stream/{rel_clean}"
            # Permission flag for client convenience
            try:
                uid = str(user.get('id')) if isinstance(user, dict) else ''
            except Exception:
                uid = ''
            resp['can_delete'] = bool(is_admin(user) or (doc.get('created_by') and str(doc.get('created_by')) == uid))
            resp_safe = _json_safe(resp)
            return jsonify(resp_safe), 201
    except Exception as e:
        return jsonify({'error': f'clip creation error: {e}'}), 500


@bp.delete('/api/cameras/<id>/clips/<clip_id>')
def api_delete_clip(id, clip_id):
    """Delete a clip record and its file. Only the creator or an admin may delete."""
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if user is None:
        return jsonify({'error': 'forbidden'}), 403
    try:
        oid = ObjectId(clip_id)
    except Exception:
        return jsonify({'error': 'invalid id'}), 400
    doc = db.clips.find_one({'_id': oid, 'camera_id': str(ObjectId(id))})
    if not doc:
        return jsonify({'error': 'not found'}), 404
    # Permission: admin or creator
    creator = str(doc.get('created_by') or '')
    user_id = ''
    try:
        user_id = str(user.get('id')) if isinstance(user, dict) else ''
    except Exception:
        user_id = ''
    if not (is_admin(user) or (creator and creator == user_id)):
        return jsonify({'error': 'forbidden'}), 403
    # Delete file on disk (best-effort)
    rel = doc.get('path_rel')
    if rel:
        cam_root = os.path.join(_recordings_root(), str(ObjectId(id)))
        abs_path = os.path.abspath(os.path.join(cam_root, rel))
        # Ensure within cam_root
        if abs_path.startswith(os.path.abspath(cam_root)) and os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
            except Exception:
                pass
    # Remove DB doc
    try:
        db.clips.delete_one({'_id': oid})
    except Exception:
        pass
    return jsonify({'status': 'deleted', 'id': clip_id})


@bp.get('/cameras/<id>/clips/<path:relpath>')
def download_clip(id, relpath):
    """Serve MP4 clip files for download. Accepts relpath that may or may not start with 'clips/'."""
    # Normalize relpath to be relative inside the camera's clips directory
    # Strip leading 'clips/' if present
    rel_norm = relpath[6:] if relpath.startswith('clips/') else relpath
    cam_clips_dir = os.path.join(_recordings_root(), str(ObjectId(id)), 'clips')
    safe_root = os.path.abspath(cam_clips_dir)
    target_dir = os.path.abspath(os.path.join(safe_root, os.path.dirname(rel_norm)))
    if not target_dir.startswith(safe_root):
        return ("Forbidden", 403)
    filename = os.path.basename(rel_norm)
    full = os.path.join(target_dir, filename)
    if not os.path.isfile(full):
        return ("Not found", 404)
    # Force MP4 mimetype and attachment download with correct filename
    return send_from_directory(target_dir, filename, mimetype='video/mp4', as_attachment=True, download_name=filename)


@bp.get('/cameras/<id>/clips/stream/<path:relpath>')
def stream_clip(id, relpath):
    """Stream MP4 clip for inline playback (no attachment). Accepts relpath that may or may not start with 'clips/'."""
    rel_norm = relpath[6:] if relpath.startswith('clips/') else relpath
    cam_clips_dir = os.path.join(_recordings_root(), str(ObjectId(id)), 'clips')
    safe_root = os.path.abspath(cam_clips_dir)
    target_dir = os.path.abspath(os.path.join(safe_root, os.path.dirname(rel_norm)))
    if not target_dir.startswith(safe_root):
        return ("Forbidden", 403)
    filename = os.path.basename(rel_norm)
    full = os.path.join(target_dir, filename)
    if not os.path.isfile(full):
        return ("Not found", 404)
    return send_from_directory(target_dir, filename, mimetype='video/mp4')


@bp.patch('/api/cameras/<id>/clips/<clip_id>')
def api_update_clip(id, clip_id):
    """Update clip metadata (currently supports renaming). Only creator or admin can edit."""
    db = current_app.db  # type: ignore[attr-defined]
    user = current_app.get_current_user()  # type: ignore[attr-defined]
    if user is None:
        return jsonify({'error': 'forbidden'}), 403
    try:
        oid = ObjectId(clip_id)
    except Exception:
        return jsonify({'error': 'invalid id'}), 400
    doc = db.clips.find_one({'_id': oid, 'camera_id': str(ObjectId(id))})
    if not doc:
        return jsonify({'error': 'not found'}), 404
    # Permission check
    creator = str(doc.get('created_by') or '')
    try:
        user_id = str(user.get('id')) if isinstance(user, dict) else ''
    except Exception:
        user_id = ''
    if not (is_admin(user) or (creator and creator == user_id)):
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    new_name = (body.get('name') or '').strip()
    if new_name == '':
        new_name = None
    try:
        db.clips.update_one({'_id': oid}, {'$set': {'name': new_name, 'updated_at': datetime.utcnow()}})
        ndoc = db.clips.find_one({'_id': oid})
        if not ndoc:
            return jsonify({'error': 'not found'}), 404
        d = _json_safe(dict(ndoc))
        d['id'] = str(ndoc.get('_id'))
        rel = d.get('path_rel')
        if rel:
            rel_clean = rel[6:] if rel.startswith('clips/') else rel
            d['download_url'] = f"/cameras/{id}/clips/{rel_clean}"
            d['stream_url'] = f"/cameras/{id}/clips/stream/{rel_clean}"
        # Permission flag remains consistent for client
        d['can_delete'] = True  # at this point, request passed permission check
        return jsonify(d)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return None


@bp.get("/api/cameras/<id>/hls_playlist")
def api_concat_hls_playlist(id):
    """Concatenate HLS minute playlists between start/end into a single VOD playlist.

    Query params:
      start: ISO datetime
      end: ISO datetime
    """
    start = request.args.get("start")
    end = request.args.get("end")
    now = datetime.utcnow()
    start_dt = _parse_iso(start) if start else now - timedelta(minutes=60)
    end_dt = _parse_iso(end) if end else now
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    # Build base dir
    base = os.path.join(_recordings_root(), str(ObjectId(id)))
    if not os.path.isdir(base):
        return Response("#EXTM3U\n#EXT-X-ENDLIST\n", mimetype='application/vnd.apple.mpegurl')

    # Collect minute directories in range
    minute_paths = []  # list of (dt, relpath, absdir)
    for y in sorted(os.listdir(base)):
        yp = os.path.join(base, y)
        if not os.path.isdir(yp):
            continue
        for m in sorted(os.listdir(yp)):
            mp = os.path.join(yp, m)
            if not os.path.isdir(mp):
                continue
            for d in sorted(os.listdir(mp)):
                dp = os.path.join(mp, d)
                if not os.path.isdir(dp):
                    continue
                for h in sorted(os.listdir(dp)):
                    hp = os.path.join(dp, h)
                    if not os.path.isdir(hp):
                        continue
                    for mi2 in sorted(os.listdir(hp)):
                        mip = os.path.join(hp, mi2)
                        if not os.path.isdir(mip):
                            continue
                        try:
                            dt = datetime(int(y), int(m), int(d), int(h), int(mi2))
                        except Exception:
                            continue
                        if dt < start_dt or dt > end_dt:
                            continue
                        if not os.path.isfile(os.path.join(mip, 'index.m3u8')):
                            continue
                        rel = os.path.join(y, m, d, h, mi2).replace('\\','/')
                        minute_paths.append((dt, rel, mip))
    minute_paths.sort(key=lambda x: x[0])

    # Build concatenated playlist
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    max_target = 0.0
    media_seq = 0
    first_pdt = None
    first = True  # first segment overall
    for dt, rel, mip in minute_paths:
        pl = os.path.join(mip, 'index.m3u8')
        try:
            with open(pl, 'r', encoding='utf-8', errors='ignore') as pf:
                last_dur = None
                cur_pdt = None
                minute_first = True  # first segment within this minute
                for raw in pf:
                    line = raw.strip()
                    if not line:
                        continue
                    if line.startswith('#EXT-X-TARGETDURATION:'):
                        try:
                            td = float(line.split(':',1)[1])
                            if td > max_target:
                                max_target = td
                        except Exception:
                            pass
                    elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
                        try:
                            cur_pdt = line.split(':',1)[1].strip()
                            if first_pdt is None:
                                first_pdt = cur_pdt
                        except Exception:
                            cur_pdt = None
                    elif line.startswith('#EXTINF:'):
                        try:
                            dur_str = line.split(':',1)[1].strip()
                            if dur_str.endswith(','):
                                dur_str = dur_str[:-1]
                            last_dur = float(dur_str)
                            if last_dur > max_target:
                                max_target = last_dur
                        except Exception:
                            last_dur = None
                    elif not line.startswith('#'):
                        # segment uri
                        if first:
                            # Set media sequence only once at start
                            lines.append(f"#EXT-X-MEDIA-SEQUENCE:{media_seq}")
                            if max_target:
                                lines.append(f"#EXT-X-TARGETDURATION:{ceil(max_target)}")
                            if first_pdt:
                                lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{first_pdt}")
                            first = False
                        elif minute_first:
                            # Insert discontinuity only at minute boundary (first seg of each subsequent minute)
                            lines.append('#EXT-X-DISCONTINUITY')
                            if cur_pdt:
                                lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{cur_pdt}")
                        # After handling boundary, mark minute as started
                        minute_first = False
                        # Append segment
                        dur_val = last_dur if last_dur is not None else 2.0
                        lines.append(f"#EXTINF:{dur_val:.3f},")
                        abs_uri = f"/cameras/{id}/recordings/{rel}/{line}"
                        lines.append(abs_uri)
                        media_seq += 1
        except Exception:
            continue

    lines.append('#EXT-X-ENDLIST')
    body = "\n".join(lines) + "\n"
    return Response(body, mimetype='application/vnd.apple.mpegurl')


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
