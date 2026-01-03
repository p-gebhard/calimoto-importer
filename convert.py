#!/usr/bin/env python3
"""
Convert a planned route (export.py --type planned) to a completed ride.
"""

import argparse
import json
import math
import requests

from datetime import datetime
from pathlib import Path

import numpy as np
from hmmlearn import hmm


CACHE_FILE   = Path(__file__).parent / "elevation_cache.json"
PROFILE_FILE = Path(__file__).parent / "ride_profile.json"


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


def _slopes(points: list, altitudes: list, window_m: float, clip: float) -> list[float]:
    n = min(len(points), len(altitudes))
    if n < 2:
        return [0.0] * n
    cum = [0.0] * n
    for i in range(1, n):
        cum[i] = cum[i - 1] + _haversine_m(points[i - 1], points[i])
    half = window_m / 2
    slopes = [0.0] * n
    for i in range(n):
        a = i
        while a > 0 and cum[i] - cum[a] < half:
            a -= 1
        b = i
        while b < n - 1 and cum[b] - cum[i] < half:
            b += 1
        dd = cum[b] - cum[a]
        s = 100.0 * (altitudes[b] - altitudes[a]) / dd if dd > 1.0 else 0.0
        slopes[i] = max(-clip, min(clip, s))
    return slopes


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


def _load_profile() -> dict:
    if not PROFILE_FILE.exists():
        raise FileNotFoundError(f"{PROFILE_FILE} not found — run calibrate.py first")
    profile = json.loads(PROFILE_FILE.read_text())
    required = {"type", "n_states", "slope_window_m", "slope_clip", "speed_max",
                "angle_mean", "steepness_mean", "speed_mean", "startprob", "transmat"}
    if profile.get("type") != "gaussian_hmm" or not required <= profile.keys():
        raise ValueError(
            f"{PROFILE_FILE} is missing or outdated — re-run calibrate.py"
        )
    return profile


def _decode_states(angles: list[float], steepness: list[float], profile: dict) -> np.ndarray:
    k = profile["n_states"]
    model = hmm.GaussianHMM(n_components=k, covariance_type="diag")
    model.startprob_ = np.asarray(profile["startprob"])
    model.transmat_ = np.asarray(profile["transmat"])
    model.means_ = np.column_stack([profile["angle_mean"], profile["steepness_mean"]])
    model.covars_ = np.column_stack([profile["angle_var"], profile["steepness_var"]])
    obs = np.column_stack([np.asarray(angles, dtype=float), np.asarray(steepness, dtype=float)])
    return model.predict(obs)


def _simulate_ride(
    distances: list[float],
    angles: list[float],
    steepness: list[float],
    max_speed: float,
    seed: int | None = None,
) -> tuple[list, list]:
    profile = _load_profile()
    states = _decode_states(angles, steepness, profile)
    speed_mean = profile["speed_mean"]
    speed_std = [math.sqrt(v) for v in profile["speed_var"]]

    rng = np.random.default_rng(seed)
    speeds: list[float] = []
    prev = min(max_speed, speed_mean[states[0]])
    for st in states:
        mu, sd = speed_mean[st], speed_std[st]
        s = min(max_speed, max(0.5, rng.normal(mu - (mu - prev) / 3, sd)))
        speeds.append(s)
        prev = s

    dates = [0.0]
    cum_s = 0.0
    for i, dist in enumerate(distances):
        seg_speed = (speeds[i] + speeds[i + 1]) / 2
        cum_s += dist / seg_speed
        dates.append(cum_s * 1000)  # Calimoto stores timestamps in milliseconds
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
    parser.add_argument(
        "--max-speed",
        type=float,
        default=None,
        metavar="KMH",
        help="Cap speed at KMH km/h (default: maximum speed from the training data)",
    )
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
    angles = (
        [0.0]
        + [
            _turn_angle(points[i - 1], points[i], points[i + 1])
            for i in range(1, len(points) - 1)
        ]
        + [0.0]
    )  # one turn angle per point (0 at the endpoints)
    profile = _load_profile()
    slopes = _slopes(
        points, altitudes, profile["slope_window_m"], profile["slope_clip"]
    )  # signed grade per point, computed from the fetched elevations
    steepness = [abs(s) for s in slopes]  # speed depends on |grade|, not its sign
    max_speed = profile["speed_max"]
    if args.max_speed is not None:
        max_speed = min(max_speed, args.max_speed / 3.6)  # km/h → m/s, never above training max
    print(f"  Speed cap: {max_speed * 3.6:.1f} km/h")
    speeds, dates = _simulate_ride(distances, angles, steepness, max_speed)

    distance = int(sum(distances))
    duration = round(dates[-1] / 1000)  # dates are ms; duration is seconds
    ride_date = args.date or datetime.now().strftime("%Y-%m-%d")
    tour = {
        "name": args.name or meta.get("name", f"Tour vom {ride_date}"),
        "distance": distance,
        "duration": duration,
        "speedAverage": round(distance / duration, 2) if duration else 0,
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
