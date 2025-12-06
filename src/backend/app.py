import os
import sys

# Ensure src directory is importable (supports both local runs and Docker)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "..")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from backend.app import create_app  # type: ignore


# Create WSGI application using the new blueprint-based factory
app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=app.config["APP_PORT"], debug=True)
