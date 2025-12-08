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
        self._stop = threading.Event()
        self._cap = None

    def stop(self):
        self._stop.set()
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
            if self._stop.is_set():
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
        while not self._stop.is_set():
            now = time.time()
            if now - last < frame_interval:
                time.sleep(0.002)
                continue
            last = now
            if self._cap is None:
                # Try reconnect
                self._cap = self._open()
                if self._cap is None:
                    time.sleep(1.0)
                    continue
            ok, frame = self._cap.read()
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

    def start(self):
        self._ctl_stop.clear()
        self._supervisor.start()
        self._janitor.start()

    def stop(self):
        self._ctl_stop.set()
        for t in list(self._threads.values()):
            try:
                t.stop()
            except Exception:
                pass
        # don't join indefinitely in teardown

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
                th = self._threads.get(cid)
                if th is None or not th.is_alive() or getattr(th, 'mode', 'jpeg') != mode:
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
                        self._threads[cid] = rec
                        rec.start()
                    else:
                        fps = float(c.get('max_fps') or 5.0)
                        jpeg_q = int(c.get('jpeg_quality') or 75)
                        connect_timeout = float(c.get('connect_timeout') or 10.0)
                        rec2 = CameraRecorder(cid, rtsp, self.out_dir, fps=fps, jpeg_quality=jpeg_q,
                                              connect_timeout=connect_timeout)
                        rec2.mode = 'jpeg'  # type: ignore
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


class HLSRecorder(threading.Thread):
    """Record RTSP to HLS segments using ffmpeg, rolling output per minute.

    Layout per camera:
      <out_dir>/<cam_id>/YYYY/MM/DD/HH/MM/index.m3u8 + seg_*.ts
    """
    def __init__(self, cam_id: str, rtsp_url: str, out_dir: str, bitrate_kbps: int = 1500, segment_seconds: int = 2):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.out_dir = out_dir
        self.bitrate_kbps = max(100, int(bitrate_kbps))
        self.segment_seconds = max(1, int(segment_seconds))
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    def stop(self):
        self._stop.set()
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass

    def _minute_dir(self, ts: datetime) -> str:
        return os.path.join(
            self.out_dir,
            self.cam_id,
            ts.strftime('%Y'), ts.strftime('%m'), ts.strftime('%d'), ts.strftime('%H'), ts.strftime('%M')
        )

    def _start_ffmpeg(self, out_dir: str):
        _ensure_dir(out_dir)
        playlist = os.path.join(out_dir, 'index.m3u8')
        # Build ffmpeg command
        # - Use TCP for RTSP to be more reliable
        # - Force keyframes ~ every 1s for better seeking (-g and -keyint_min)
        keyint = max(1, min(2 * self.segment_seconds, 2)) * 30  # rough for 30fps sources
        bitrate = f"{self.bitrate_kbps}k"
        cmd = [
            'ffmpeg', '-y', '-rtsp_transport', 'tcp', '-i', self.rtsp_url,
            '-an',
            '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
            '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', str(self.bitrate_kbps * 2) + 'k',
            '-g', str(keyint), '-sc_threshold', '0',
            '-hls_time', str(self.segment_seconds), '-hls_list_size', '0',
            # Add program_date_time to expose wall-clock per-segment timestamps for precise seeking
            '-hls_flags', 'independent_segments+program_date_time',
            '-f', 'hls', playlist
        ]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self._proc = None

    def run(self):
        if not self.rtsp_url:
            return
        current_minute = None
        while not self._stop.is_set():
            now = datetime.utcnow()
            minute_dir = self._minute_dir(now)
            if current_minute != minute_dir:
                # rotate: stop previous ffmpeg and start new in the new minute folder
                if self._proc and self._proc.poll() is None:
                    try:
                        self._proc.terminate()
                        # give it a moment to exit
                        for _ in range(10):
                            if self._proc.poll() is not None:
                                break
                            time.sleep(0.1)
                    except Exception:
                        pass
                self._start_ffmpeg(minute_dir)
                current_minute = minute_dir
            # If ffmpeg crashed, try restart in the same minute dir
            if self._proc is None or self._proc.poll() is not None:
                self._start_ffmpeg(current_minute or minute_dir)
            # Sleep a bit, but wake to minute boundary
            time.sleep(0.5)
        # Teardown
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass
