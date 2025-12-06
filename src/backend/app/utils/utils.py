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
