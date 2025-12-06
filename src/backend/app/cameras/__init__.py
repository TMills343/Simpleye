# Re-export the blueprint so imports like `from src.backend.app.cameras import bp` work
from .routes import bp  # noqa: F401

__all__ = ["bp"]