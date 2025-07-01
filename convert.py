#!/usr/bin/env python3
"""
Convert a planned route (export.py --type planned) to a completed ride.

Usage:
    python convert.py <planned-folder> --out <output-folder>
    python convert.py <planned-folder> --out <output-folder> --date 2021-07-04 --name "My Ride"
"""

import argparse
import json
import requests
import time

from datetime import datetime
from pathlib import Path


CACHE_FILE = Path(__file__).parent / "elevation_cache.json"


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _cache_key(p) -> str:
    return f"{round(p[0], 6)},{round(p[1], 6)}"


def _fetch_from_open_elevation(uncached_pts: list, cache: dict) -> list[float | None]:
    fetched: list[float | None] = []
    offset = 0
    for chunk in _chunks(uncached_pts, 200):
        try:
            r = requests.post(
                "https://api.open-elevation.com/api/v1/lookup",
                json={
                    "locations": [
                        {"latitude": p[0], "longitude": p[1]} for p in chunk
                    ]
                },
                timeout=30,
            )
            r.raise_for_status()
            vals = [item["elevation"] for item in r.json()["results"]]
            fetched.extend(vals)
            for j, v in enumerate(vals):
                cache[_cache_key(uncached_pts[offset + j])] = v
            CACHE_FILE.write_text(json.dumps(cache))
        except Exception:
            fetched.extend([None] * len(chunk))
        offset += len(chunk)
    return fetched


def _fetch_from_open_topo_data(uncached_pts: list, cache: dict) -> list[float | None]:
    fetched = []
    offset = 0
    for chunk in _chunks(uncached_pts, 100):
        try:
            r = requests.post(
                "https://api.opentopodata.org/v1/eudem25m",
                json={
                    "locations": "|".join(f"{p[0]},{p[1]}" for p in chunk),
                    "interpolation": "cubic",
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if data["status"] == "OK":
                vals = [item["elevation"] for item in data["results"]]
                fetched.extend(vals)
                for j, v in enumerate(vals):
                    cache[_cache_key(uncached_pts[offset + j])] = v
                CACHE_FILE.write_text(json.dumps(cache))
            else:
                fetched.extend([None] * len(chunk))
        except Exception:
            fetched.extend([None] * len(chunk))
        offset += len(chunk)
    return fetched


def _fetch_elevations(points: list) -> list[float]:
    cache: dict = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}

    uncached_pts = [p for p in points if _cache_key(p) not in cache]
    n_cached = len(points) - len(uncached_pts)

    if not uncached_pts:
        print(f"  Elevation: all {len(points)} points from cache.")
    else:
        print(
            f"  Elevation: fetching {len(uncached_pts)} points ({n_cached} cached) ...",
            end=" ",
            flush=True,
        )

        fetched = _fetch_from_open_elevation(uncached_pts, cache)

        if all(v is None or v == 0 for v in fetched):
            print("falling back to opentopodata ...", end=" ", flush=True)
            _fetch_from_open_topo_data(uncached_pts, cache)

    return [float(cache.get(_cache_key(p), 0)) for p in points]


def main():
    parser = argparse.ArgumentParser(
        description="Convert a planned route to a completed tour"
    )
    parser.add_argument(
        "input", help="Planned route folder (output of export.py --type planned)"
    )
    parser.add_argument("--out", required=True, metavar="DIR", help="Output folder")

    args = parser.parse_args()

    in_dir = Path(args.input)
    for f in (in_dir / "meta.json", in_dir / "points.json"):
        if not f.exists():
            raise ValueError(f"Error: {f} not found")

    meta = json.loads((in_dir / "meta.json").read_text(encoding="utf-8"))
    points = json.loads((in_dir / "points.json").read_text(encoding="utf-8"))["points"]
    print(f"Input: {in_dir.name}  ({len(points)} points)")

    altitudes = _fetch_elevations(points)
    distances = []
    angles = []
    speeds = []
    dates = []

    ride_date = datetime.now().strftime("%Y-%m-%d")
    tour = {
        "name": f"Tour vom {ride_date}",
        "distance": 0,
        "duration": 0,
        "speedAverage": 0,
        "speedMax": 0,
        "altitudeMax": 0,
        "altitudeMin": 0,
        "altitudeIncline": 0,
        "altitudeDecline": 0,
        "regionCode": meta.get("regionCode", "EU_NO_0000"),
        "language": meta.get("language", "de"),
        "typeTrack": "TRACKING",
        "device": "android",
        "timeCreated": {"__type": "Date", "iso": f"{ride_date}T10:00:00.000Z"},
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "points.json").write_text(json.dumps({"points": points}))
    (out_dir / "altitudes.json").write_text(json.dumps({"altitudes": altitudes}))
    (out_dir / "speeds.json").write_text(json.dumps({"speeds": speeds}))
    (out_dir / "dates.json").write_text(json.dumps({"dates": dates}))
    (out_dir / "tour.json").write_text(json.dumps(tour, indent=2))

    print(f"Output: {out_dir}/")


if __name__ == "__main__":
    main()
