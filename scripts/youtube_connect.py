#!/usr/bin/env python3
"""Connect a Desktop OAuth client using Google's local loopback + PKCE flow."""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from paperspeak import db, youtube


def main():
    parser = argparse.ArgumentParser(description="Connect your YouTube account for private automatic uploads")
    parser.add_argument("client_json", type=Path, help="Google Cloud Desktop OAuth client JSON")
    args = parser.parse_args()
    data = json.loads(args.client_json.expanduser().read_text(encoding="utf-8"))
    client = data.get("installed")
    if not isinstance(client, dict) or not client.get("client_id"):
        raise SystemExit("Choose a Google Cloud Desktop OAuth client JSON.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != "/youtube-oauth" or query.get("state", [None])[0] != state:
                self.send_error(400, "Invalid OAuth state")
                return
            if query.get("error"):
                result["error"] = query["error"][0]
            elif query.get("code"):
                result["code"] = query["code"][0]
            else:
                result["error"] = "Missing authorization code"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>You may return to PaperSpeak.</h1></body></html>")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 2
    port = server.server_port
    redirect = f"http://127.0.0.1:{port}/youtube-oauth"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect,
        "response_type": "code", "scope": youtube.SCOPE, "access_type": "offline",
        "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    print("Open this Google authorization URL in a browser:", url, sep="\n", flush=True)
    print(f"If the browser is on your Mac, first open an SSH tunnel: ssh -L {port}:127.0.0.1:{port} kuwabara@192.168.10.112", flush=True)
    deadline = time.monotonic() + 600
    while not result and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    if not result.get("code"):
        raise SystemExit("Google authorization did not finish: " + result.get("error", "timed out"))
    response = httpx.post(youtube.TOKEN_URL, data={
        "code": result["code"], "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""), "redirect_uri": redirect,
        "code_verifier": verifier, "grant_type": "authorization_code"}, timeout=30)
    response.raise_for_status()
    token = response.json()
    if not token.get("refresh_token") or youtube.SCOPE not in token.get("scope", youtube.SCOPE).split():
        raise SystemExit("Google did not grant offline YouTube upload access.")
    private = youtube._secret_dir()
    youtube._write_private(private / "client.json", {"installed": client})
    youtube._write_private(private / "token.json", {
        "access_token": token["access_token"], "refresh_token": token["refresh_token"],
        "expires_at": time.time() + token.get("expires_in", 3600),
        "refresh_expires_at": time.time() + token["refresh_token_expires_in"] if token.get("refresh_token_expires_in") else None,
        "scope": youtube.SCOPE})
    db.init()
    db.set_setting("youtube_auto_upload", True)
    print("YouTube connected. When the complete lesson video is ready, it will upload privately.")


if __name__ == "__main__":
    main()
