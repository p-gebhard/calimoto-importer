#!/usr/bin/env python3
"""
Import a converted tour (output of convert.py) into Calimoto.

Usage:
    python import.py <converted-folder>
    python import.py converted/sandnes_moskenes
"""

import argparse
import getpass
import io
import json
import os
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from staticmap import StaticMap, Line

load_dotenv()

APP_ID = os.environ["CALIMOTO_APP_ID"]
JS_KEY = os.environ["CALIMOTO_JS_KEY"]
BASE_URL = os.environ["CALIMOTO_BASE_URL"]
CLIENT_VER = os.environ["CALIMOTO_CLIENT_VER"]


def _auth(session_token: str) -> dict:
    return {
        "_ApplicationId": APP_ID,
        "_JavaScriptKey": JS_KEY,
        "_ClientVersion": CLIENT_VER,
        "_InstallationId": str(uuid.uuid4()),
        "_SessionToken": session_token,
    }


def _post(path: str, body: dict) -> dict:
    resp = requests.post(
        f"{BASE_URL}/{path}",
        data=json.dumps(body),
        headers={"Content-Type": "text/plain"},
    )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def login(username: str, password: str) -> tuple[str, str]:
    data = _post(
        "login",
        {
            "_ApplicationId": APP_ID,
            "_JavaScriptKey": JS_KEY,
            "_ClientVersion": CLIENT_VER,
            "_InstallationId": str(uuid.uuid4()),
            "username": username,
            "password": password,
        },
    )
    return data["sessionToken"], data["objectId"]


def upload_file(session_token: str, filename: str, data: bytes) -> dict:
    resp = requests.post(
        f"{BASE_URL}/files/{filename}",
        data=data,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Parse-Application-Id": APP_ID,
            "X-Parse-JavaScript-Key": JS_KEY,
            "X-Parse-Session-Token": session_token,
        },
    )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    r = resp.json()
    return {"__type": "File", "name": r["name"], "url": r["url"]}


def render_preview(points: list) -> bytes:
    m = StaticMap(600, 400)
    coords = [(lon, lat) for lat, lon in points]
    m.add_line(Line(coords, "#e5433a", 3))
    img = m.render()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_track(session_token: str, user_id: str, tour: dict, files: dict) -> str:
    data = _post(
        "classes/tblTracks",
        {
            **_auth(session_token),
            **tour,
            **files,
            "userId": user_id,
            "creatorId": user_id,
        },
    )
    return data["objectId"]


def main():
    parser = argparse.ArgumentParser(
        description="Import a converted tour into Calimoto"
    )
    parser.add_argument("input", help="Converted tour folder (output of convert.py)")
    args = parser.parse_args()

    in_dir = Path(args.input)
    for name in (
        "tour.json",
        "points.json",
        "altitudes.json",
        "speeds.json",
        "dates.json",
    ):
        if not (in_dir / name).exists():
            print(f"Error: {in_dir / name} not found")
            sys.exit(1)

    tour = json.loads((in_dir / "tour.json").read_text(encoding="utf-8"))
    points = json.loads((in_dir / "points.json").read_text(encoding="utf-8"))["points"]

    username = os.environ.get("CALIMOTO_USERNAME", "").strip()
    password = os.environ.get("CALIMOTO_PASSWORD", "").strip()
    if not username:
        username = input("Username: ").strip()
    if not password:
        password = getpass.getpass("Password: ")

    print(f"Logging in as {username} ...", end=" ", flush=True)
    try:
        session_token, user_id = login(username, password)
    except RuntimeError as e:
        print(f"\nLogin failed: {e}")
        sys.exit(1)
    print("OK")

    files = {}
    for key in ("points", "altitudes", "speeds", "dates"):
        print(f"Uploading {key}.json ...", end=" ", flush=True)
        try:
            files[key] = upload_file(
                session_token, f"{key}.json", (in_dir / f"{key}.json").read_bytes()
            )
        except RuntimeError as e:
            print(f"\nUpload failed: {e}")
            sys.exit(1)
        print("OK")

    print("Rendering preview ...", end=" ", flush=True)
    try:
        preview_png = render_preview(points)
        files["image"] = upload_file(session_token, "preview.png", preview_png)
    except Exception as e:
        print(f"skipped ({e})")  # preview is optional, don't abort
    else:
        print("OK")

    print(f"Creating track '{tour['name']}' ...", end=" ", flush=True)
    try:
        oid = create_track(session_token, user_id, tour, files)
    except RuntimeError as e:
        print(f"\nFailed: {e}")
        sys.exit(1)
    print(f"OK  ({oid})")


if __name__ == "__main__":
    main()
