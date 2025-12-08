import socket
from datetime import datetime
from typing import Dict, Any, Optional

from bson import ObjectId


def to_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    return d


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
    # Optional per-camera FPS cap (float). Accept keys: max_fps or fps
    max_fps_val = data.get("max_fps", data.get("fps"))
    max_fps: Optional[float]
    try:
        max_fps = float(max_fps_val) if max_fps_val not in (None, "") else None
        if max_fps is not None and max_fps <= 0:
            max_fps = None
    except (TypeError, ValueError):
        max_fps = None
    # Optional per-camera JPEG quality (1-100)
    jpeg_q_val = data.get("jpeg_quality")
    jpeg_quality: Optional[int]
    try:
        jq = int(jpeg_q_val) if jpeg_q_val not in (None, "") else None
        if jq is None or jq < 1 or jq > 100:
            jpeg_quality = None
        else:
            jpeg_quality = jq
    except (TypeError, ValueError):
        jpeg_quality = None
    # Optional per-camera streaming timeouts/intervals (floats, >0)
    def _float_opt(key: str) -> Optional[float]:
        val = data.get(key)
        try:
            if val in (None, ""):
                return None
            f = float(val)
            if f <= 0:
                return None
            return f
        except (TypeError, ValueError):
            return None
    connect_timeout = _float_opt("connect_timeout")
    idle_reconnect = _float_opt("idle_reconnect")
    heartbeat_interval = _float_opt("heartbeat_interval")
    notes = (data.get("notes") or "").strip()
    enabled = str(data.get("enabled") or data.get("enabled", "on")).lower() in ["1", "true", "on", "yes"]
    # Optional per-camera retention in hours (int)
    retention_val = data.get("retention_hours")
    retention_hours: Optional[int]
    try:
        if retention_val in (None, ""):
            retention_hours = None
        else:
            rh = int(retention_val)
            retention_hours = rh if rh > 0 else None
    except (TypeError, ValueError):
        retention_hours = None
    # Recording mode normalization: default to 'hls' unless explicitly set to 'jpeg'
    rec_mode_raw = (data.get("recording_mode") or "").strip().lower()
    recording_mode = rec_mode_raw if rec_mode_raw in ("jpeg", "hls") else "hls"
    return {
        "name": name,
        "ip": ip,
        "http_port": http_port,
        "rtsp_url": rtsp_url,
        "max_fps": max_fps,
        "jpeg_quality": jpeg_quality,
        "connect_timeout": connect_timeout,
        "idle_reconnect": idle_reconnect,
        "heartbeat_interval": heartbeat_interval,
        "notes": notes,
        "enabled": enabled,
        "retention_hours": retention_hours,
        # Recording mode and options (optional)
        # recording_mode: 'hls' (default) or 'jpeg'
        "recording_mode": recording_mode,
        # HLS bitrate in kbps (int)
        "hls_bitrate_kbps": (lambda v: (int(v) if str(v).strip() != "" else None)) (data.get("hls_bitrate_kbps")),
        # HLS segment length in seconds (int)
        "hls_segment_seconds": (lambda v: (int(v) if str(v).strip() != "" else None)) (data.get("hls_segment_seconds")),
    }


def get_current_user(db, session) -> Optional[Dict[str, Any]]:
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


__all__ = [
    "to_doc",
    "check_port_open",
    "normalize_camera_payload",
    "get_current_user",
    "datetime",
]
