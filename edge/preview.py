"""Tiny live-preview server (MJPEG) so the annotated feed can be watched in the
browser - including on the dashboard's Live page - without a desktop window.

  GET /stream.mjpg   live annotated video
  GET /snapshot.jpg  latest frame
  GET /status        JSON counters (fps, cache, incidents)
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np


class PreviewServer:
    def __init__(self, host: str, port: int, width: int):
        self.width = width
        self.frame: bytes | None = None
        self.status: dict = {}
        self.cond = threading.Condition()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")

            def do_GET(self):
                if self.path.startswith("/stream.mjpg"):
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            with server.cond:
                                server.cond.wait(timeout=2.0)
                                jpg = server.frame
                            if jpg is None:
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                            time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                elif self.path.startswith("/snapshot.jpg") and server.frame:
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", "image/jpeg")
                    self.end_headers()
                    self.wfile.write(server.frame)
                elif self.path.startswith("/status"):
                    body = json.dumps(server.status).encode()
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="preview", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()

    def update(self, frame: np.ndarray, status: dict) -> None:
        h, w = frame.shape[:2]
        if w > self.width:
            frame = cv2.resize(frame, (self.width, int(h * self.width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with self.cond:
                self.frame = buf.tobytes()
                self.status = status
                self.cond.notify_all()
