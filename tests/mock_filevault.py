"""Atrapa FileVault: serwer HTTP w wątku, zapamiętuje, co i z jakim ciasteczkiem odpytano."""
import threading
import unicodedata
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        srv = self.server
        path = self.path.split("?")[0]
        srv.seen.append((path, self.headers.get("Cookie")))
        route = srv.routes.get(path)
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, headers, body = route
        headers = dict(headers)
        no_cl = headers.pop("X-No-CL", None)
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        if no_cl is None and "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class MockFileVault:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.routes, self.httpd.seen = {}, []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    @property
    def seen(self):
        return self.httpd.seen

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def file(self, path, body, name=None, ctype="application/octet-stream", extra=None):
        h = {"Content-Type": ctype}
        if name:
            # jak Werkzeug send_file: wariant ASCII + filename*=UTF-8'' dla znaków spoza ASCII
            ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
            h["Content-Disposition"] = (f'attachment; filename="{ascii_name}"; '
                                        f"filename*=UTF-8''{quote(name)}")
        h.update(extra or {})
        self.httpd.routes[path] = (200, h, body)

    def page(self, path, html):
        self.httpd.routes[path] = (200, {"Content-Type": "text/html; charset=utf-8"}, html.encode("utf-8"))

    def raw(self, path, status, headers, body=b""):
        self.httpd.routes[path] = (status, headers, body)
