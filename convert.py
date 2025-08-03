#!/usr/bin/env python3
"""
Convert a planned route (export.py --type planned) to a completed ride.

Usage:
    python convert.py <planned-folder> --out <output-folder>
    python convert.py <planned-folder> --out <output-folder> --date 2021-07-04 --name "My Ride"
"""

import argparse
import json
import math
import random
import requests
import time

from datetime import datetime
from pathlib import Path


CACHE_FILE = Path(__file__).parent / "elevation_cache.json"


def _haversine_m(p1, p2) -> float:
    r = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _turn_angle(p1, p2, p3) -> float:
    a, b, c = _haversine_m(p2, p3), _haversine_m(p1, p3), _haversine_m(p1, p2)
    if a == 0 or c == 0:
        return 0.0
    return 180 - math.degrees(
        math.acos(max(-1.0, min(1.0, (a**2 + c**2 - b**2) / (2 * a * c))))
    )


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
                    "locations": [{"latitude": p[0], "longitude": p[1]} for p in chunk]
                },
                timeout=30,
            )
            r.raise_for_status()
            vals = [item["elevation"] for item in r.json()["results"]]
            fetched.extend(vals)
            for j, v in enumerate(vals):
                if (
                    v != 0
                ):  # open-elevation returns 0 for missing data, not actual sea level
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


def _interpolate_missing(points: list, cache: dict):
    out = [cache.get(_cache_key(p)) for p in points]
    first = next((v for v in out if v is not None), 0.0)
    for i, v in enumerate(out):
        if v is None:
            out[i] = first
        else:
            break
    start = -1
    for i, v in enumerate(out):
        if v is None and start == -1:
            start = i
        elif v is not None and start != -1:
            delta = (out[i] - out[start - 1]) / (i - start + 1)
            for j in range(start, i):
                out[j] = out[j - 1] + delta
            start = -1
    return [float(v or 0) for v in out]


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

        if all(not v for v in fetched):
            print("falling back to opentopodata ...", end=" ", flush=True)
            _fetch_from_open_topo_data(uncached_pts, cache)

    return _interpolate_missing(points, cache)


def _simulate_ride(distances: list[float], angles: list[float]) -> tuple[list, list]:
    def _max_speed(angle):
        if angle > 75:
            return 4.2  # ~15 km/h  roundabout / sharp turn
        if angle > 50:
            return 8.3  # ~30 km/h  medium curve
        if angle > 25:
            return 16.7  # ~60 km/h  light curve
        return 22.2  # ~80 km/h  straight

    rng = random.Random()
    speeds, dates, cum_t = [], [], 0.0
    prev = rng.normalvariate(3.0, 1.25)
    speeds.append(prev)
    dates.append(cum_t)

    for dist, angle in zip(distances, angles):
        ms = _max_speed(angle)
        s = max(0.1, rng.normalvariate(ms - (ms - prev) / 3, 1.25))
        speeds.append(s)
        cum_t += dist / s
        dates.append(cum_t)
        prev = s

    s = max(0.1, rng.normalvariate(3.0, 1.25))
    speeds.append(s)
    cum_t += (distances[-1] if distances else 0) / s
    dates.append(cum_t)
    return speeds, dates


def main():
    parser = argparse.ArgumentParser(
        description="Convert a planned route to a completed tour"
    )
    parser.add_argument(
        "input", help="Planned route folder (output of export.py --type planned)"
    )
    parser.add_argument("--out", required=True, metavar="DIR", help="Output folder")
    parser.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD", help="Ride date (default: today)"
    )
    parser.add_argument("--name", default=None, help="Ride name (default: from route)")
    args = parser.parse_args()

    in_dir = Path(args.input)
    for f in (in_dir / "meta.json", in_dir / "points.json"):
        if not f.exists():
            raise ValueError(f"Error: {f} not found")

    meta = json.loads((in_dir / "meta.json").read_text(encoding="utf-8"))
    points = json.loads((in_dir / "points.json").read_text(encoding="utf-8"))["points"]
    print(f"Input: {in_dir.name}  ({len(points)} points)")

    altitudes = _fetch_elevations(points)
    distances = [_haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1)]
    angles = [
        _turn_angle(points[i], points[i + 1], points[i + 2])
        for i in range(len(points) - 2)
    ]
    angles.append(0.0)
    speeds, dates = _simulate_ride(distances, angles)

    ride_date = args.date or datetime.now().strftime("%Y-%m-%d")
    tour = {
        "name": args.name or meta.get("name", f"Tour vom {ride_date}"),
        "distance": int(sum(distances)),
        "duration": round(dates[-1]),
        "speedAverage": round(sum(speeds) / len(speeds), 1),
        "speedMax": round(max(speeds), 2),
        "altitudeMax": round(max(altitudes)),
        "altitudeMin": round(min(altitudes)),
        "altitudeIncline": round(
            sum(max(0, b - a) for a, b in zip(altitudes, altitudes[1:]))
        ),
        "altitudeDecline": round(
            sum(max(0, a - b) for a, b in zip(altitudes, altitudes[1:]))
        ),
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
