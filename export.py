#!/usr/bin/env python3
"""
Export completed Calimoto rides to a local folder.

Usage:
    python export.py --out nordkapp --from 2021-07-04 --to 2021-07-22
    python export.py --out all_rides
    python export.py --out recent --limit 10

Credentials are read from .env (CALIMOTO_USERNAME / CALIMOTO_PASSWORD).
"""

import argparse
import getpass
import json
import os
import sys
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID     = os.environ["CALIMOTO_APP_ID"]
JS_KEY     = os.environ["CALIMOTO_JS_KEY"]
BASE_URL   = os.environ["CALIMOTO_BASE_URL"]
CLIENT_VER = os.environ["CALIMOTO_CLIENT_VER"]


def _auth(session_token: str) -> dict:
    return {
        "_ApplicationId":  APP_ID,
        "_JavaScriptKey":  JS_KEY,
        "_ClientVersion":  CLIENT_VER,
        "_InstallationId": str(uuid.uuid4()),
        "_SessionToken":   session_token,
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
    """Returns (session_token, user_id)."""
    data = _post("login", {
        "_ApplicationId":  APP_ID,
        "_JavaScriptKey":  JS_KEY,
        "_ClientVersion":  CLIENT_VER,
        "_InstallationId": str(uuid.uuid4()),
        "username": username,
        "password": password,
    })
    return data["sessionToken"], data["objectId"]


def get_tracks(session_token: str, user_id: str, date_from: str | None, date_to: str | None, limit: int) -> list[dict]:
    where: dict = {"userId": user_id}
    if date_from or date_to:
        f: dict = {}
        if date_from:
            f["$gte"] = {"__type": "Date", "iso": f"{date_from}T00:00:00.000Z"}
        if date_to:
            f["$lte"] = {"__type": "Date", "iso": f"{date_to}T23:59:59.999Z"}
        where["createdAt"] = f
    data = _post("classes/tblTracks", {
        **_auth(session_token),
        "_method": "GET",
        "where":   json.dumps(where),
        "limit":   limit,
        "order":   "-createdAt",
    })
    return data.get("results", [])


def _build_gpx(meta: dict, points: list, altitudes: list | None) -> str:
    name = meta.get("name", "")
    tc = meta.get("timeCreated", {})
    created = tc.get("iso", "") if isinstance(tc, dict) else str(tc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="calimoto-importer" xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <metadata><name>{name}</name></metadata>',
        "  <trk>", f"    <name>{name}</name>", "    <trkseg>",
    ]
    for i, (lat, lon) in enumerate(points):
        tpt = f'      <trkpt lat="{lat}" lon="{lon}">'
        if altitudes and i < len(altitudes):
            tpt += f"<ele>{altitudes[i]:.1f}</ele>"
        if created:
            tpt += f"<time>{created}</time>"
        lines.append(tpt + "</trkpt>")
    lines += ["    </trkseg>", "  </trk>", "</gpx>"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export completed Calimoto rides")
    parser.add_argument("--out",   required=True, metavar="DIR",        help="Output folder")
    parser.add_argument("--from",  dest="date_from", metavar="YYYY-MM-DD", help="Start date (inclusive)")
    parser.add_argument("--to",    dest="date_to",   metavar="YYYY-MM-DD", help="End date (inclusive)")
    parser.add_argument("--limit", type=int, default=10_000,            help="Max number of rides")
    args = parser.parse_args()

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

    date_hint = f" [{args.date_from or '...'} – {args.date_to or '...'}]" if (args.date_from or args.date_to) else ""
    print(f"Fetching rides{date_hint} ...", end=" ", flush=True)
    try:
        tracks = get_tracks(session_token, user_id, args.date_from, args.date_to, args.limit)
    except RuntimeError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    print(f"{len(tracks)} found")

    if not tracks:
        print("No rides found.")
        return

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tracks.json").write_text(
        json.dumps({"results": tracks}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for i, track in enumerate(tracks, 1):
        oid  = track["objectId"]
        tc   = track.get("timeCreated", {})
        date = (tc.get("iso", "") if isinstance(tc, dict) else str(tc))[:10] or "?"
        print(f"[{i:3}/{len(tracks)}] {date}  {track.get('name', oid)}")

        track_dir = out_dir / oid
        track_dir.mkdir(exist_ok=True)
        (track_dir / "meta.json").write_text(
            json.dumps(track, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        files_data: dict = {}
        for key in ("points", "altitudes", "speeds", "dates"):
            url = (track.get(key) or {}).get("url", "")
            if not url:
                continue
            try:
                data = requests.get(url, timeout=30).json()
                files_data[key] = data
                (track_dir / f"{key}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                print(f"         ✗ {key}: {e}")

        if "points" in files_data:
            pts  = files_data["points"].get("points", [])
            alts = files_data.get("altitudes", {}).get("altitudes")
            (track_dir / f"{date}_{oid}.gpx").write_text(_build_gpx(track, pts, alts), encoding="utf-8")

    total_size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    print(f"\nExported {len(tracks)} rides to {out_dir}/  ({total_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()

