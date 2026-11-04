"""Page the phone opens to send its camera to this computer.

Live video needs HTTPS. Browsers block the camera on a plain http:// address
that is not localhost. A manual photo still works over HTTP.
"""

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vision.frames import age, camera_url, set_url, update

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / ".certs"
PORT = 8765

PAGE = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UR3e camera</title>
<style>
  body { font-family: sans-serif; margin: 16px; background: #111; color: #eee; }
  video, img, canvas { width: 100%; max-height: 70vh; background: #000; }
  button, label.shot { display: block; width: 100%; margin-top: 12px; padding: 16px; font-size: 18px; }
  p { line-height: 1.4; }
  #status { color: #9d9; }
</style>
</head>
<body>
<p>Tieni il telefono sopra il tavolo. Il lato alto dello schermo guarda il fondo del tavolo, lontano dalla base del braccio.</p>
<video id="video" autoplay playsinline muted></video>
<p id="status">Apro la fotocamera…</p>
<button id="live" type="button">Invia il video</button>
<label class="shot">Se il video è bloccato, scatta una foto
  <input id="file" type="file" accept="image/*" capture="environment">
</label>
<canvas id="canvas" hidden></canvas>
<script>
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const status = document.getElementById("status");
let timer = null;

async function sendBlob(blob) {
  const response = await fetch("/frame", { method: "POST", body: blob });
  if (!response.ok) throw new Error("invio " + response.status);
  status.textContent = "Fotogramma inviato " + new Date().toLocaleTimeString();
}

function grab() {
  const w = video.videoWidth || 640;
  const scale = Math.min(1, 640 / w);
  canvas.width = Math.max(1, Math.round(w * scale));
  canvas.height = Math.max(1, Math.round((video.videoHeight || 480) * scale));
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  canvas.toBlob(blob => { if (blob) sendBlob(blob).catch(err => { status.textContent = String(err); }); }, "image/jpeg", 0.6);
}

document.getElementById("live").onclick = () => {
  if (timer) { clearInterval(timer); timer = null; status.textContent = "Video in pausa"; return; }
  timer = setInterval(grab, 700);
  grab();
};

document.getElementById("file").onchange = () => {
  const file = document.getElementById("file").files[0];
  if (file) sendBlob(file).catch(err => { status.textContent = String(err); });
};

navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
  .then(stream => { video.srcObject = stream; status.textContent = "Fotocamera aperta. Premi Invia il video."; })
  .catch(() => { status.textContent = "Il browser ha bloccato la fotocamera dal vivo. Usa il pulsante per scattare una foto, oppure accetta il certificato di questa pagina."; });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/status"):
            body = ('{"url":"%s","age":%s}' % (camera_url(), "null" if age() is None else f"{age():.1f}")).encode()
            self._bytes(200, "application/json", body)
            return
        self._bytes(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/frame"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > 2_000_000:
            self.send_error(413)
            return
        update(self.rfile.read(length))
        self.send_response(204)
        self.end_headers()

    def _bytes(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def lan_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def _openssl() -> str | None:
    candidates = [
        "openssl",
        r"C:\Program Files\Git\usr\bin\openssl.exe",
    ]
    for candidate in candidates:
        try:
            subprocess.run([candidate, "version"], check=True, capture_output=True)
            return candidate
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def ensure_cert(ip: str) -> tuple[Path, Path] | None:
    CERT_DIR.mkdir(exist_ok=True)
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    stamp = CERT_DIR / "ip.txt"
    if cert.exists() and key.exists() and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == ip:
        return cert, key
    tool = _openssl()
    if tool is None:
        return None
    subprocess.run(
        [
            tool, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "14",
            "-subj", "/CN=voice-arm",
            "-addext", f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    stamp.write_text(ip, encoding="utf-8")
    return cert, key


def start(port: int = PORT) -> str:
    ip = lan_ip()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    scheme = "http"
    pair = ensure_cert(ip)
    if pair is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(pair[0], pair[1])
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    url = f"{scheme}://{ip}:{port}"
    set_url(url)
    threading.Thread(target=httpd.serve_forever, name="phone-camera", daemon=True).start()
    return url


if __name__ == "__main__":
    print(start(), flush=True)
    threading.Event().wait()
