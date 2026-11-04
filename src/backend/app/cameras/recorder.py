import os
import threading
import time
import shutil
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import subprocess

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows local dev
    fcntl = None  # type: ignore

# Lock file (inside RECORDINGS_DIR) that ensures only one process records at a time.
# Gunicorn runs create_app() in every worker, so without this each worker would start
# its own RecorderManager and every camera would be recorded once per worker.
_LOCK_FILENAME = ".recorder.lock"
_LOCK_RETRY_SECONDS = 10.0


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _now_ts_ms() -> int:
    return int(time.time() * 1000)


class CameraRecorder(threading.Thread):
    def __init__(self, cam_id: str, rtsp_url: str, out_dir: str, fps: float = 5.0, jpeg_quality: int = 75,
                 connect_timeout: float = 10.0):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.out_dir = out_dir
        self.fps = max(0.5, float(fps))
        self.jpeg_quality = max(1, min(100, int(jpeg_quality)))
        self.connect_timeout = connect_timeout
        self._stop_evt = threading.Event()  # not _stop: that shadows Thread._stop()
        self._cap = None

    def stop(self):
        self._stop_evt.set()
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass

    def _open(self):
        if cv2 is None:
            raise RuntimeError("opencv-python-headless not installed")
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)  # type: ignore[attr-defined]
        start = time.time()
        while not cap.isOpened():
            if self._stop_evt.is_set():
                cap.release()
                return None
            if time.time() - start > self.connect_timeout:
                cap.release()
                return None
            time.sleep(0.2)
        return cap

    def run(self):
        if not self.rtsp_url:
            return
        encode_param = None
        if cv2 is not None:
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)]  # type: ignore[attr-defined]
        frame_interval = 1.0 / self.fps
        last = 0.0
        self._cap = self._open()
        while not self._stop_evt.is_set():
            if self._cap is None:
                # Try reconnect
                self._cap = self._open()
                if self._cap is None:
                    time.sleep(1.0)
                    continue
            # Pull every frame (blocks at the camera's rate) so we stay current instead of
            # falling behind and buffering; only convert/encode the frames we keep.
            ok = self._cap.grab()
            if ok:
                now = time.time()
                if now - last < frame_interval:
                    continue
                last = now
                ok, frame = self._cap.retrieve()
            if not ok:
                # reconnect on failure
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
                time.sleep(0.25)
                continue
            if cv2 is None:
                continue
            ok, buf = cv2.imencode('.jpg', frame, encode_param)  # type: ignore[attr-defined]
            if not ok:
                continue
            ts = datetime.utcnow()
            # Directory by camera/YYYY/MM/DD/HH/MM
            subdir = os.path.join(self.out_dir, self.cam_id,
                                   ts.strftime('%Y'), ts.strftime('%m'), ts.strftime('%d'),
                                   ts.strftime('%H'), ts.strftime('%M'))
            _ensure_dir(subdir)
            fname = f"{ts.strftime('%S')}_{_now_ts_ms() % 1000:03d}.jpg"
            fpath = os.path.join(subdir, fname)
            try:
                with open(fpath, 'wb') as f:
                    f.write(buf.tobytes())
            except Exception:
                # Disk or permission errors; skip frame
                time.sleep(0.1)


class RecorderManager:
    def __init__(self, db, out_dir: str):
        self.db = db
        self.out_dir = out_dir
        _ensure_dir(out_dir)
        self._threads: Dict[str, threading.Thread] = {}
        self._ctl_stop = threading.Event()
        self._supervisor = threading.Thread(target=self._supervise, daemon=True)
        self._janitor = threading.Thread(target=self._janitor_loop, daemon=True)
        self._lock_fd: Optional[int] = None

    def start(self):
        self._ctl_stop.clear()
        # Only the process holding the lock records. The others keep retrying so that
        # if the recording worker dies (e.g. gunicorn restarts it), another takes over.
        threading.Thread(target=self._acquire_lock_then_run, daemon=True).start()

    def _try_lock(self) -> bool:
        if fcntl is None:
            return True
        path = os.path.join(self.out_dir, _LOCK_FILENAME)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as e:
            print(f"[Recorder] Cannot open lock file {path}: {e}")
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def _acquire_lock_then_run(self):
        while not self._ctl_stop.is_set():
            if self._try_lock():
                print(f"[Recorder] pid {os.getpid()} holds the recorder lock; starting recording")
                self._supervisor.start()
                self._janitor.start()
                return
            self._ctl_stop.wait(_LOCK_RETRY_SECONDS)

    def stop(self):
        self._ctl_stop.set()
        for t in list(self._threads.values()):
            try:
                t.stop()
            except Exception:
                pass
        # don't join indefinitely in teardown
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)  # releases the flock
            except OSError:
                pass
            self._lock_fd = None

    def _supervise(self):
        # Periodically sync recorders with DB state
        while not self._ctl_stop.is_set():
            try:
                cams = list(self.db.cameras.find())
            except Exception:
                cams = []
            active_ids = set()
            for c in cams:
                cid = str(c.get('_id'))
                enabled = bool(c.get('enabled', True))
                rtsp = (c.get('rtsp_url') or '').strip()
                if not enabled or not rtsp:
                    continue
                active_ids.add(cid)
                # Determine recording mode (default to HLS if unset)
                mode = (c.get('recording_mode') or 'hls').lower()
                # Everything the recorder is built from; if any of it changes (e.g. the
                # RTSP URL is edited), restart the recorder so the change takes effect.
                settings = (mode, rtsp) + tuple(
                    c.get(k) for k in ('hls_bitrate_kbps', 'hls_segment_seconds',
                                       'max_fps', 'jpeg_quality', 'connect_timeout'))
                th = self._threads.get(cid)
                if th is None or not th.is_alive() or getattr(th, 'settings', None) != settings:
                    # Stop existing
                    if th is not None and hasattr(th, 'stop'):
                        try:
                            th.stop()  # type: ignore
                        except Exception:
                            pass
                    # Start appropriate recorder
                    if mode == 'hls':
                        bitrate_kbps = None
                        try:
                            bitrate_kbps = int(c.get('hls_bitrate_kbps') or 1500)
                        except Exception:
                            bitrate_kbps = 1500
                        seg_seconds = None
                        try:
                            seg_seconds = int(c.get('hls_segment_seconds') or 2)
                        except Exception:
                            seg_seconds = 2
                        rec = HLSRecorder(cid, rtsp, self.out_dir, bitrate_kbps=bitrate_kbps, segment_seconds=seg_seconds)
                        rec.mode = 'hls'  # type: ignore
                        rec.settings = settings  # type: ignore
                        self._threads[cid] = rec
                        rec.start()
                    else:
                        fps = float(c.get('max_fps') or 5.0)
                        jpeg_q = int(c.get('jpeg_quality') or 75)
                        connect_timeout = float(c.get('connect_timeout') or 10.0)
                        rec2 = CameraRecorder(cid, rtsp, self.out_dir, fps=fps, jpeg_quality=jpeg_q,
                                              connect_timeout=connect_timeout)
                        rec2.mode = 'jpeg'  # type: ignore
                        rec2.settings = settings  # type: ignore
                        self._threads[cid] = rec2
                        rec2.start()
            # Stop threads for cameras no longer active
            for cid, t in list(self._threads.items()):
                if cid not in active_ids or not t.is_alive():
                    try:
                        if hasattr(t, 'stop'):
                            t.stop()  # type: ignore
                    except Exception:
                        pass
                    self._threads.pop(cid, None)
            # Sleep before next sync
            for _ in range(10):
                if self._ctl_stop.is_set():
                    break
                time.sleep(1.0)

    def _janitor_loop(self):
        # Run every 5 minutes
        while not self._ctl_stop.is_set():
            self._run_retention_cleanup()
            for _ in range(300):
                if self._ctl_stop.is_set():
                    break
                time.sleep(1.0)

    def _run_retention_cleanup(self):
        # For each camera, delete directories older than retention_hours
        try:
            cams = list(self.db.cameras.find())
        except Exception:
            cams = []
        now = datetime.utcnow()
        for c in cams:
            cid = str(c.get('_id'))
            rh = c.get('retention_hours')
            try:
                rh_int = int(rh) if rh is not None else 24
            except Exception:
                rh_int = 24
            cutoff = now - timedelta(hours=rh_int)
            cam_root = os.path.join(self.out_dir, cid)
            if not os.path.isdir(cam_root):
                continue
            # Traverse nested dirs and delete if dir time < cutoff
            # We compute dir time from path names; if parsing fails use mtime.
            for y in os.listdir(cam_root):
                yp = os.path.join(cam_root, y)
                if not os.path.isdir(yp):
                    continue
                for m in os.listdir(yp):
                    mp = os.path.join(yp, m)
                    if not os.path.isdir(mp):
                        continue
                    for d in os.listdir(mp):
                        dp = os.path.join(mp, d)
                        if not os.path.isdir(dp):
                            continue
                        for h in os.listdir(dp):
                            hp = os.path.join(dp, h)
                            if not os.path.isdir(hp):
                                continue
                            for mi in os.listdir(hp):
                                mip = os.path.join(hp, mi)
                                if not os.path.isdir(mip):
                                    continue
                                # Build time from path
                                try:
                                    dt = datetime(int(y), int(m), int(d), int(h), int(mi))
                                except Exception:
                                    dt = datetime.utcfromtimestamp(os.path.getmtime(mip))
                                if dt < cutoff:
                                    try:
                                        shutil.rmtree(mip, ignore_errors=True)
                                    except Exception:
                                        pass


# Codecs that browsers can play from fMP4 HLS, mapped to the sample-entry tag to write.
# "hvc1" (not ffmpeg's default "hev1") is required for H.265 playback in Safari/Chrome.
COPY_CODEC_TAGS = {'h264': 'avc1', 'hevc': 'hvc1'}
# ffmpeg exiting sooner than this after start counts as a failure and triggers backoff.
_FAST_FAIL_SECONDS = 10.0
_MAX_BACKOFF_SECONDS = 60.0
# Re-check the camera's codec periodically in case it is changed on the camera itself.
_CODEC_RECHECK_SECONDS = 15 * 60


def probe_video_codec(rtsp_url: str, timeout: float = 20.0) -> Optional[str]:
    """Return the codec name of the first video stream (e.g. 'h264', 'hevc'), or None."""
    cmd = ['ffprobe', '-v', 'error', '-rtsp_transport', 'tcp', '-select_streams', 'v:0',
           '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', rtsp_url]
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=timeout, text=True).stdout
    except Exception:
        return None
    lines = [ln.strip().lower() for ln in out.splitlines() if ln.strip()]
    return lines[0] if lines else None


class HLSRecorder(threading.Thread):
    """Record RTSP to HLS segments using ffmpeg, rolling output per minute.

    H.264 and H.265 sources are stream-copied: no decoding or re-encoding, so recording
    costs almost no CPU and keeps the camera's original quality. Layout per minute:
      <out_dir>/<cam_id>/YYYY/MM/DD/HH/MM/index.m3u8 + init.mp4 + seg_*.m4s
    Other codecs are transcoded to H.264 at bitrate_kbps (the previous behaviour):
      <out_dir>/<cam_id>/YYYY/MM/DD/HH/MM/index.m3u8 + index*.ts
    """
    def __init__(self, cam_id: str, rtsp_url: str, out_dir: str, bitrate_kbps: int = 1500, segment_seconds: int = 2):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.out_dir = out_dir
        self.bitrate_kbps = max(100, int(bitrate_kbps))
        self.segment_seconds = max(1, int(segment_seconds))
        # Not named _stop: that would shadow threading.Thread._stop().
        self._stop_evt = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._codec: Optional[str] = None
        self._codec_checked_at = 0.0
        self._started_at = 0.0
        self._failures = 0
        self._retry_at = 0.0

    def stop(self):
        self._stop_evt.set()
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    def _log(self, msg: str):
        print(f"[Recorder] camera {self.cam_id}: {msg}", flush=True)

    def _minute_dir(self, ts: datetime) -> str:
        return os.path.join(
            self.out_dir,
            self.cam_id,
            ts.strftime('%Y'), ts.strftime('%m'), ts.strftime('%d'), ts.strftime('%H'), ts.strftime('%M')
        )

    def _build_cmd(self, out_dir: str):
        playlist = os.path.join(out_dir, 'index.m3u8')
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            # TCP for RTSP is more reliable; genpts fills in missing timestamps from some cameras
            '-rtsp_transport', 'tcp', '-fflags', '+genpts', '-i', self.rtsp_url,
            '-map', '0:v:0', '-an',
        ]
        tag = COPY_CODEC_TAGS.get(self._codec or '')
        if tag:
            cmd += ['-c:v', 'copy', '-tag:v', tag]
        else:
            # Force keyframes so segments can be cut at segment_seconds
            keyint = max(1, min(2 * self.segment_seconds, 2)) * 30  # rough for 30fps sources
            bitrate = f"{self.bitrate_kbps}k"
            cmd += [
                '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
                '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', str(self.bitrate_kbps * 2) + 'k',
                '-g', str(keyint), '-sc_threshold', '0',
            ]
        cmd += [
            '-f', 'hls', '-hls_time', str(self.segment_seconds), '-hls_list_size', '0',
            # program_date_time exposes wall-clock per-segment timestamps for precise seeking
            '-hls_flags', 'independent_segments+program_date_time',
        ]
        if tag:
            # Stream-copied segments are cut at the camera's own keyframes.
            cmd += [
                '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
                '-hls_segment_filename', os.path.join(out_dir, 'seg_%05d.m4s'),
            ]
        cmd.append(playlist)
        return cmd

    def _start_ffmpeg(self, out_dir: str) -> bool:
        if self._codec is None:
            codec = probe_video_codec(self.rtsp_url)
            if codec is None:
                self._schedule_retry("cannot read the stream (camera unreachable or bad URL)")
                return False
            self._set_codec(codec)
        if self._stop_evt.is_set():
            return False
        _ensure_dir(out_dir)
        try:
            self._proc = subprocess.Popen(self._build_cmd(out_dir), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self._proc = None
            self._schedule_retry(f"cannot start ffmpeg: {e}")
            return False
        self._started_at = time.time()
        return True

    def _set_codec(self, codec: str):
        if codec != self._codec:
            how = 'stream copy' if codec in COPY_CODEC_TAGS else f'transcode to H.264 at {self.bitrate_kbps}kbps'
            self._log(f"video codec {codec}; recording with {how}")
        self._codec = codec
        self._codec_checked_at = time.time()

    def _recheck_codec(self):
        """Probe while ffmpeg keeps recording; restart only if the camera's codec changed."""
        self._codec_checked_at = time.time()
        codec = probe_video_codec(self.rtsp_url)
        if codec and codec != self._codec:
            self._set_codec(codec)
            self._terminate()

    def _schedule_retry(self, reason: str):
        """Back off exponentially so a broken camera doesn't respawn ffmpeg twice a second."""
        self._failures += 1
        delay = min(2 ** self._failures, _MAX_BACKOFF_SECONDS)
        self._retry_at = time.time() + delay
        self._log(f"{reason}; retrying in {delay:.0f}s")

    def _terminate(self):
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
        except Exception:
            pass

    def run(self):
        if not self.rtsp_url:
            return
        current_minute = None
        while not self._stop_evt.is_set():
            minute_dir = self._minute_dir(datetime.utcnow())
            if self._proc is not None and self._proc.poll() is not None:
                # ffmpeg exited on its own (camera dropped, stream error, ...)
                code = self._proc.returncode
                self._proc = None
                if self._stop_evt.is_set():
                    break
                if time.time() - self._started_at < _FAST_FAIL_SECONDS:
                    self._codec = None  # re-probe in case the stream changed
                    self._schedule_retry(f"ffmpeg exited after {time.time() - self._started_at:.0f}s (code {code})")
                else:
                    self._failures = 0  # it was recording fine; restart right away
            if self._proc is not None and time.time() - self._codec_checked_at > _CODEC_RECHECK_SECONDS:
                self._recheck_codec()
            if self._proc is not None and current_minute != minute_dir:
                # Rotate into the new minute folder
                self._terminate()
            if self._proc is None and time.time() >= self._retry_at:
                if self._start_ffmpeg(minute_dir):
                    current_minute = minute_dir
            self._stop_evt.wait(0.5)
        self._terminate()
