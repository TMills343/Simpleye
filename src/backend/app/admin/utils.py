def is_admin(user) -> bool:
    try:
        return bool(user) and "admin" in (user.get("roles") or [])
    except Exception:
        return False
