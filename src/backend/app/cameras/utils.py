import threading
import time
from typing import Dict, Optional, Tuple

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - env without opencv
    cv2 = None  # type: ignore


def open_capture(rtsp: str, connect_timeout: float):
    if cv2 is None:
        raise RuntimeError("OpenCV is not available. Install opencv-python-headless.")
    cap = cv2.VideoCapture(rtsp, cv2.CAP_FFMPEG)  # type: ignore[attr-defined]
    start = time.time()
    while not cap.isOpened():
        if time.time() - start > connect_timeout:
            cap.release()
            raise RuntimeError("Failed to open RTSP stream (timeout)")
        time.sleep(0.2)
    return cap


# Keep a feed's camera connection open this long after its last viewer leaves, so
# page reloads and navigating between the dashboard and a camera page reuse it.
_FEED_IDLE_SECONDS = 15.0

_feeds: Dict[Tuple, "LiveFeed"] = {}
_feeds_lock = threading.Lock()


class LiveFeed:
    """One RTSP reader per camera (and live-view settings), shared by every viewer in
    this process. It pulls every frame so it never falls behind the camera, but only
    converts and JPEG-encodes frames at the configured fps, once for all viewers.
    """

    def __init__(self, key: Tuple, rtsp: str, fps: float, jpeg_quality: int,
                 connect_timeout: float, idle_reconnect: float):
        self.key = key
        self.rtsp = rtsp
        self.frame_interval = 1.0 / max(1.0, float(fps))
        self.jpeg_quality = int(jpeg_quality)
        self.connect_timeout = float(connect_timeout)
        self.idle_reconnect = float(idle_reconnect)
        self.cond = threading.Condition()
        self.frame: Optional[bytes] = None
        self.seq = 0
        self.error: Optional[str] = None
        self.viewers = 0  # guarded by _feeds_lock
        self.last_viewer_left = time.time()
        threading.Thread(target=self._run, daemon=True, name=f"live-feed-{rtsp[-24:]}").start()

    def _should_exit(self) -> bool:
        with _feeds_lock:
            if self.viewers == 0 and time.time() - self.last_viewer_left > _FEED_IDLE_SECONDS:
                if _feeds.get(self.key) is self:
                    del _feeds[self.key]
                return True
        return False

    def _publish(self, frame: Optional[bytes] = None, error: Optional[str] = None):
        with self.cond:
            if frame is not None:
                self.frame = frame
                self.seq += 1
                self.error = None
            if error is not None:
                self.error = error
            self.cond.notify_all()

    def _run(self):
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality] if cv2 is not None else None  # type: ignore[attr-defined]
        cap = None
        next_due = 0.0
        last_ok = time.time()
        try:
            while not self._should_exit():
                if cap is None:
                    try:
                        cap = open_capture(self.rtsp, self.connect_timeout)
                        last_ok = time.time()
                    except Exception as e:
                        self._publish(error=f"RTSP error: {e}")
                        time.sleep(2.0)
                        continue
                # grab() blocks until the next frame arrives, pacing this loop at the camera's rate
                if not cap.grab():
                    if time.time() - last_ok > self.idle_reconnect:
                        cap.release()
                        cap = None
                    else:
                        time.sleep(0.05)
                    continue
                last_ok = time.time()
                if last_ok < next_due:
                    continue  # decoded to stay in sync, but not needed for the output fps
                # Schedule from the previous due time (not "now") so the output averages the
                # configured fps even when it doesn't divide the camera's frame rate evenly.
                next_due = max(next_due + self.frame_interval, last_ok - self.frame_interval)
                ok, frame = cap.retrieve()
                if not ok:
                    continue
                ok, buf = cv2.imencode('.jpg', frame, encode_param)  # type: ignore[attr-defined]
                if not ok:
                    continue
                self._publish(frame=buf.tobytes())
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass


def _acquire_feed(rtsp: str, fps: float, jpeg_quality: int, connect_timeout: float, idle_reconnect: float) -> LiveFeed:
    key = (rtsp, float(fps), int(jpeg_quality), float(connect_timeout), float(idle_reconnect))
    with _feeds_lock:
        feed = _feeds.get(key)
        if feed is None:
            feed = LiveFeed(key, rtsp, fps, jpeg_quality, connect_timeout, idle_reconnect)
            _feeds[key] = feed
        feed.viewers += 1
        return feed


def _release_feed(feed: LiveFeed):
    with _feeds_lock:
        feed.viewers -= 1
        if feed.viewers <= 0:
            feed.viewers = 0
            feed.last_viewer_left = time.time()


def mjpeg_generator(rtsp: str, fps: float, jpeg_quality: int, connect_timeout: float, idle_reconnect: float, heartbeat_interval: float):
    boundary = b"--frame"
    feed = _acquire_feed(rtsp, fps, jpeg_quality, connect_timeout, idle_reconnect)
    try:
        last_seq = 0
        give_up_at = time.time() + connect_timeout + 2.0
        while True:
            with feed.cond:
                feed.cond.wait_for(lambda: feed.seq != last_seq, timeout=max(0.2, heartbeat_interval))
                seq, data, err = feed.seq, feed.frame, feed.error
            if seq != last_seq and data is not None:
                last_seq = seq
                yield (boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode()
                       + b"\r\n\r\n" + data + b"\r\n")
                continue
            if seq == 0 and err and time.time() > give_up_at:
                # Never got a frame: report the error like before and end the stream
                yield boundary + b"\r\nContent-Type: text/plain\r\n\r\n" + err.encode() + b"\r\n"
                return
            # Heartbeat keeps the connection alive and detects viewers who have left
            yield boundary + b"\r\nContent-Type: text/plain\r\n\r\n." + b"\r\n"
    finally:
        _release_feed(feed)
