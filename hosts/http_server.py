"""Local browser host for testing.

Serves a simple chat page on 127.0.0.1 for any subject.

Run: python3 hosts/http_server.py --deployment statistics
     python3 hosts/http_server.py --deployment marketing
     python3 hosts/http_server.py --deployment software_engineering
"""
import argparse
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deployments  # noqa: E402


def make_server(bot, root, port=8765):
    # Display settings from deployments.build(), or generic defaults.
    ui = getattr(bot, "ui", {
        "deployment": "custom", "display_name": bot.conversation_title.lower(),
        "brand": "TA", "sample_questions": [], "placeholder_hint": "Ask a question about this topic...",
    })

    def snap(token):
        result = bot.snapshot(token)
        result["ui"] = ui
        return result

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, status, body, content_type="application/json", cookie=None):
            data = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if cookie: self.send_header("Set-Cookie", f"fyp_session={cookie}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(data)
        def valid_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")
        def token(self):
            jar = SimpleCookie()
            try: jar.load(self.headers.get("Cookie", ""))
            except Exception: return None
            token = jar["fyp_session"].value if "fyp_session" in jar else None
            return token if token in bot.sessions else None
        def do_GET(self):
            if not self.valid_host(): return self.respond(403, {"error": "Invalid host"})
            path = urlparse(self.path).path
            if path == "/api/state":
                token = self.token()
                if not token: return self.respond(401, {"error": "Start a new session."})
                return self.respond(200, snap(token))
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if path not in files: return self.respond(404, {"error": "Not found"})
            name, kind = files[path]
            self.respond(200, (root / "static" / name).read_bytes(), kind)
        def do_POST(self):
            if not self.valid_host(): return self.respond(403, {"error": "Invalid host"})
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://" + self.headers.get("Host", ""):
                return self.respond(403, {"error": "Cross-origin requests are not allowed"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "Use application/json"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384: raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict): raise ValueError("Expected a JSON object")
                if self.path == "/api/session":
                    token = bot.new_session()
                    return self.respond(200, snap(token), cookie=token)
                token = self.token()
                if not token: return self.respond(401, {"error": "Start a new session."})
                if self.path == "/api/chat":
                    result = bot.chat(token, payload.get("query"), payload.get("scenario"))
                elif self.path == "/api/survey":
                    result = bot.submit_survey(token, payload.get("ratings"))
                else: return self.respond(404, {"error": "Not found"})
                result["ui"] = ui
                self.respond(200, result)
            except (ValueError, TypeError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(500, {"error": "Local host error; the request was not completed."})
    return HTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", default="statistics", choices=deployments.available() or ["statistics"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", choices=("offline", "azure"), default="offline")
    parser.add_argument("--database", default=None)
    args = parser.parse_args()
    bot = deployments.build(args.deployment, mode=args.model, database=args.database)
    server = make_server(bot, deployments.ROOT, args.port)
    print(f"Plug-and-play HTTP host: http://127.0.0.1:{server.server_port} (deployment={args.deployment}, model={args.model})", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
