- Local development with docker-compose: docker-compose automatically loads variables from the `.env` file in the project root and injects them into the `web` container via the `environment` section in `docker-compose.yml`. No `env_file` is required.
- Portainer or other orchestrators: define MONGO_URI, MONGO_DB, and FLASK_SECRET_KEY as environment variables for the stack/service. The application reads these directly from the container environment. No `.env` file is needed in the container.
- The MongoDB service in docker-compose is behind a profile named `local-db`. It will only be created/started if you pass `--profile local-db`. This prevents creating a local MongoDB when using an external MONGO_URI.

Recording and review
1) Configure cameras
   - Cameras → Add/Edit: set a name, IP/port, and RTSP URL.
   - Recording Mode defaults to HLS (recommended). You can change to JPEG if needed.
   - Optional per-camera settings: Retention (hours), HLS Bitrate (kbps), HLS Segment length (seconds), FPS cap/JPEG quality (JPEG mode).
2) Storage & retention
   - Container writes to RECORDINGS_DIR, mounted by docker-compose at /data/recordings.
   - Layout per camera: /data/recordings/<camera_id>/YYYY/MM/DD/HH/MM/
     - HLS minutes: index.m3u8 + seg_*.ts
     - JPEG minutes: SS_ms.jpg files
   - A background janitor deletes minute folders older than each camera’s retention_hours (default 24h).
3) Review page
   - From a camera’s Live View, click “Review”.
   - Set From/To and “Load timeline”. The left panel shows a collapsible Day → Hour → Minute tree. Expanding a minute shows per-second ticks.
   - Click a second to jump. Controls: Play/Pause/Stop, ±5s, Speed. HLS playback is seamless across minute boundaries.
4) Clips
   - Right “Clips” panel → Make a clip → set Start/Stop (defaults to current time), Create.
   - Each clip shows a green Play button (modal playback) and a ⋮ menu with Download and (if permitted) Delete.
   - Double-click a clip’s name to rename.

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
  - GET /cameras/<id>/review — Review page with timeline/playback/clips
  - GET /cameras/<id>/stream.mjpg — MJPEG stream for embedding
  - GET /cameras/<id>/recordings/<path> — Serve recorded files (JPEG frames, HLS playlists/segments)
  - GET /cameras/<id>/clips/<path> — Download a generated clip (MP4 attachment)
  - GET /cameras/<id>/clips/stream/<path> — Stream a generated clip (inline MP4)
  - GET,POST /signup — First-boot only; create initial admin user
  - GET,POST /login — User login
  - GET /logout — End session
- API
  - GET /api/cameras — List cameras
  - POST /api/cameras — Create camera (JSON body)
  - GET /api/cameras/<id> — Get camera
  - POST /api/cameras/<id> — Update camera (form/JSON; admin)
  - DELETE /api/cameras/<id> — Delete camera
  - GET /api/cameras/<id>/recordings?start=<iso>&end=<iso> — List minute buckets and files; detects JPEG vs HLS
  - GET /api/cameras/<id>/hls_playlist?start=<iso>&end=<iso> — On-the-fly unified HLS playlist across a time range
  - GET /api/cameras/<id>/clips — List clips for a camera
  - POST /api/cameras/<id>/clips — Create a clip (HLS only) with { start, end, name? }
  - PATCH /api/cameras/<id>/clips/<clip_id> — Rename a clip (creator or admin)
  - DELETE /api/cameras/<id>/clips/<clip_id> — Delete a clip (creator or admin)

Notes
- The “Check” action only tests TCP connectivity to the configured HTTP port (default 80). It does not authenticate to or stream from the camera.
- Live view translates RTSP video into an MJPEG stream using OpenCV + FFmpeg in the backend. For most cameras, provide a full RTSP URL (e.g., rtsp://user:pass@192.168.1.10:554/stream). Network/firewall rules must allow the app container to reach the camera on its RTSP ports.
- Recording uses FFmpeg for HLS (default) or OpenCV for JPEG frames. Ensure ffmpeg is available (installed in the Docker image by default).
- Storage and retention: recordings live under RECORDINGS_DIR (mounted by docker-compose at /data/recordings). Retention cleanup runs every 5 minutes.
- Permissions: only admins can create/edit cameras; clip deletion is allowed to the clip’s creator or admins. Viewers can create clips but cannot delete others’ clips.

Static assets structure
- static/css — shared styles (e.g., style.css)
- static/js — client-side scripts (e.g., main.js)
- static/images — images/placeholders
- static/scripts — optional helper scripts/assets
