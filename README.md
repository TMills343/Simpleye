### Simpleye — LAN IP Camera Dashboard (Flask + MongoDB)

Simple, dockerized Flask application with MongoDB storage for tracking LAN IP cameras. Add cameras, record notes and RTSP URL, and quickly check whether the camera’s HTTP port is reachable from the server.

Features
- Add, edit, delete cameras (name, IP, HTTP port, RTSP URL, notes, enabled)
- One-click “Check” to test TCP connectivity to each camera’s HTTP port
- Live video preview via MJPEG for cameras with RTSP URL (no plugin; works in browser)
- Dashboard grid showing all enabled RTSP cameras (Frigate-style multi-view)
- MongoDB-backed storage (configurable via .env)
- REST JSON API for cameras
- Dockerfile and docker-compose for easy deployment
- Basic user management: first-boot admin signup, login/logout, password hashing

Requirements
- Docker and Docker Compose

Quick start (Docker Compose)
1. Create a .env file at the repository root (you can copy the existing `.env` as a starting point) and set your values if needed.
2. (Optional) Edit .env to customize settings. By default, the app will connect to the `mongo` service provided by compose.
3. Start services:
   - `docker compose up --build`
4. Open the app at: http://localhost:8000

First-time setup (users)
- On first boot when the database has no users, you will be redirected to a Signup page to create the initial admin account (username + password; email optional for future 2FA/reset).
- After creating the admin, you'll be taken to the Login page. All application routes (UI/API/streams) require authentication.

Environment variables (.env)
- MONGO_URI: MongoDB connection string (default: mongodb://mongo:27017)
- MONGO_DB: Database name (default: simpleye)
- FLASK_SECRET_KEY: Flask secret (set a strong value in production)

How env is applied
- Local development with docker-compose: docker-compose automatically loads variables from the `.env` file in the project root and injects them into the `web` container via the `environment` section in `docker-compose.yml`. No `env_file` is required.
- Portainer or other orchestrators: define MONGO_URI, MONGO_DB, and FLASK_SECRET_KEY as environment variables for the stack/service. The application reads these directly from the container environment. No `.env` file is needed in the container.

Local development (without Docker)
1. Python 3.12 recommended.
2. Create and activate a virtualenv.
3. `pip install -r requirements.txt`
4. Create a `.env` file and set `MONGO_URI` to a MongoDB instance you have running locally. You may also set `MONGO_DB` and `FLASK_SECRET_KEY`.
5. Run the app: `python app.py` then browse http://localhost:8000

Routes
- UI
  - GET / — Dashboard grid of all enabled cameras with RTSP
  - GET /dashboard — Same as /
  - GET /cameras — Camera list (table view)
  - GET,POST /cameras/new — Add camera
  - GET,POST /cameras/<id>/edit — Edit camera
  - POST /cameras/<id>/delete — Delete camera
  - POST /cameras/<id>/check — Check reachability of camera’s HTTP port
  - GET /cameras/<id>/view — Live viewer page for a camera
  - GET /cameras/<id>/stream.mjpg — MJPEG stream for embedding
  - GET,POST /signup — First-boot only; create initial admin user
  - GET,POST /login — User login
  - GET /logout — End session
- API
  - GET /api/cameras — List cameras
  - POST /api/cameras — Create camera (JSON body)
  - GET /api/cameras/<id> — Get camera
  - PATCH /api/cameras/<id> — Update camera
  - DELETE /api/cameras/<id> — Delete camera

Notes
- The “Check” action only tests TCP connectivity to the configured HTTP port (default 80). It does not authenticate to or stream from the camera.
- Live view translates RTSP video into an MJPEG stream using OpenCV + FFmpeg in the backend. For most cameras, provide a full RTSP URL (e.g., rtsp://user:pass@192.168.1.10:554/stream). Network/firewall rules must allow the app container to reach the camera on its RTSP ports.
- For production, ensure FLASK_SECRET_KEY is set and consider hardening MongoDB (auth, network rules, backups).

Static assets structure
- static/css — shared styles (e.g., style.css)
- static/js — client-side scripts (e.g., main.js)
- static/images — images/placeholders
- static/scripts — optional helper scripts/assets
