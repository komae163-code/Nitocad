"""Browser smoke test for the shared import-workspace modules (needs playwright + chromium; offline)."""
import http.server, json, socketserver, threading, sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
class Q(http.server.SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=str(ROOT), **k)
    def log_message(s, *a): pass
def serve():
    srv = socketserver.TCPServer(("127.0.0.1", 0), Q); threading.Thread(target=srv.serve_forever, daemon=True).start(); return srv
def launch(p):
    return p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"])
if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    srv = serve(); port = srv.server_address[1]
    with sync_playwright() as p:
        b = launch(p); pg = b.new_page(); logs = []
        pg.on("console", lambda m: logs.append(m.text)); pg.on("pageerror", lambda e: logs.append("PAGEERROR " + str(e)))
        pg.goto(f"http://127.0.0.1:{port}/tests/ui/harness_core.html"); pg.wait_for_function("document.title==='done'", timeout=20000)
        R = pg.evaluate("window.__R"); print(json.dumps(R, indent=1)); print("logs:", logs[:5]); b.close()
