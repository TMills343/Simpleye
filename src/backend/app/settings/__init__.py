# Re-export the blueprint so it can be imported as `from src.backend.app.settings import bp`
from .routes import bp  # noqa: F401

__all__ = ["bp"]