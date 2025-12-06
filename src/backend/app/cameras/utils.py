import time

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


def mjpeg_generator(rtsp: str, fps: float, jpeg_quality: int, connect_timeout: float, idle_reconnect: float, heartbeat_interval: float):
    boundary = b"--frame"
    try:
        cap = open_capture(rtsp, connect_timeout)
    except Exception as e:
        msg = f"RTSP error: {e}".encode()
        yield boundary + b"\r\nContent-Type: text/plain\r\n\r\n" + msg + b"\r\n"
        return
    last = time.time()
    last_send = time.time()
    encode_param = None
    if cv2 is not None:
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]  # type: ignore[attr-defined]
    frame_interval = 1.0 / max(1.0, float(fps))
    try:
        while True:
            now = time.time()
            if now - last < frame_interval:
                time.sleep(0.001)
                # Heartbeat
                if now - last_send > heartbeat_interval:
                    yield boundary + b"\r\nContent-Type: text/plain\r\n\r\n." + b"\r\n"
                    last_send = now
                continue
            last = now
            ok, frame = cap.read()
            if not ok:
                # Reconnect periodically on idle
                if now - last_send > idle_reconnect:
                    cap.release()
                    try:
                        cap = open_capture(rtsp, connect_timeout)
                    except Exception:
                        time.sleep(1)
                    continue
                time.sleep(0.05)
                continue
            if cv2 is None:
                # Should not happen because open_capture would have failed already
                time.sleep(0.05)
                continue
            ok, buf = cv2.imencode('.jpg', frame, encode_param)  # type: ignore[attr-defined]
            if not ok:
                continue
            data = buf.tobytes()
            chunk = boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n"
            yield chunk
            last_send = time.time()
    finally:
        try:
            cap.release()
        except Exception:
            pass
