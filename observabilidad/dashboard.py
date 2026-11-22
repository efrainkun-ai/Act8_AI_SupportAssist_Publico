"""Sirve solo el tablero y el estado agregado, en la máquina local."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    files = {
        "/": (Path(__file__).with_name("dashboard.html"), "text/html"),
        "/estado.json": (args.evidence / "estado.json", "application/json"),
    }

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            content = files.get(self.path)
            if not content or not content[0].is_file():
                self.send_error(404)
                return
            body = content[0].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content[1] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler) as server:
        print(f"Tablero en http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
