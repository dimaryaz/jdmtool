from __future__ import annotations

import http.server
import json
import shutil
import socketserver
import urllib.parse
import webbrowser

from http import HTTPStatus
from pathlib import Path


import requests


SSO_DIR = Path(__file__).parent / "resources" / "flygarmin"
SSO_HTML = SSO_DIR / "index.html"
SSO_JS = SSO_DIR / "index.js"

SSO_CLIENT_ID = "FLY_GARMIN_DESKTOP"

SERVICES_HOST = "https://services.garmin.com"


class GarminHandler(http.server.BaseHTTPRequestHandler):
    def handle_credentials(self, auth: dict[str, str]):
        ...

    def _get_path(self) -> str:
        url = urllib.parse.urlsplit(self.path, scheme="http", allow_fragments=False)
        return url.path

    def do_GET(self) -> None:
        path = self._get_path()

        if path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(SSO_HTML.stat().st_size))
            self.end_headers()

            with open(SSO_HTML, "rb") as fd:
                shutil.copyfileobj(fd, self.wfile)
        elif path == "/sso.js":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(SSO_JS.stat().st_size))
            self.end_headers()

            with open(SSO_JS, "rb") as fd:
                shutil.copyfileobj(fd, self.wfile)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        content = self.rfile.read(length)

        path = self._get_path()
        if path == "/login":
            data = json.loads(content)
            service_url = data["serviceUrl"]
            service_ticket = data["serviceTicket"]
            print("Received ticket; requesting access token...")

            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

            resp = requests.post(
                f"{SERVICES_HOST}/api/oauth/token",
                data={
                    'grant_type': 'service_ticket',
                    'client_id': SSO_CLIENT_ID,
                    'service_url': service_url,
                    'service_ticket': service_ticket,
                },
                timeout=5,
            )
            resp.raise_for_status()
            print("Received access token")
            self.handle_credentials(resp.json())
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def do_login() -> dict[str, str]:
    credentials: dict[str, str] | None = None

    class Handler(GarminHandler):
        def handle_credentials(self, auth: dict[str, str]):
            nonlocal credentials
            credentials = auth

    with socketserver.TCPServer(("localhost", 0), Handler) as httpd:
        host, port = httpd.server_address[:2]
        url = f"http://{host}:{port}"
        print(f"Serving at {url}")
        webbrowser.open(url)
        while not credentials:
            httpd.handle_request()

    return credentials
