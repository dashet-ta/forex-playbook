#!/usr/bin/env python3
"""
SMC Backtest — local dev server
Serves the app on http://localhost:8081
Persists all data to data/trades.json via /api/data
"""

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8081
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data', 'trades.json')


class BacktestHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api/'):
            self._handle_api_get()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/'):
            self._handle_api_post()
        else:
            self.send_response(404)
            self.end_headers()

    # ── API handlers ──────────────────────────────

    def _handle_api_get(self):
        route = self.path[len('/api/'):]

        if route == 'health':
            self._json({'status': 'ok', 'server': 'smc_backtest'})

        elif route == 'data':
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    raw = f.read()
                body = raw.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._json({'backtests': [], 'activeBtId': None})

        else:
            self._json({'error': f'Unknown route: /api/{route}'}, status=404)

    def _handle_api_post(self):
        route = self.path[len('/api/'):]

        if route == 'data':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                parsed = json.loads(body)
                os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
                with open(DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(parsed, f, ensure_ascii=False, indent=2)
                self._json({'status': 'saved'})
            except Exception as e:
                self._json({'error': str(e)}, status=400)

        else:
            self._json({'error': f'Unknown route: /api/{route}'}, status=404)

    # ── Helpers ───────────────────────────────────

    def _json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, fmt, *args):
        print(f'  {self.address_string()} — {fmt % args}')


if __name__ == '__main__':
    os.chdir(BASE_DIR)
    server = HTTPServer(('', PORT), BacktestHandler)
    print(f'SMC Backtest server → http://localhost:{PORT}')
    print(f'Data file           → {DATA_FILE}')
    print('Press Ctrl+C to stop.\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped.')
