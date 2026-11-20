"""Local live dashboard for the DACH improvement loop.

  python -m scripts.dach.dashboard [--port 8765]      then open http://localhost:8765/dashboard/
Serves radar-backtest/dach/ with caching disabled; the page polls results/loop_state.json every 2 s, so it is live
whenever scripts.dach.loop runs on the same machine (or after `git pull` of a finished run).
"""
from __future__ import annotations

import argparse
import functools
import http.server

from .io import DACH


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    handler = functools.partial(NoCache, directory=str(DACH))
    print(f"dashboard: http://localhost:{a.port}/dashboard/  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler).serve_forever()


if __name__ == "__main__":
    main()
