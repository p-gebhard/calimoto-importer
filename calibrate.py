#!/usr/bin/env python3
"""
Derive a speed profile from real exported rides and save it as ride_profile.json.
Run this once before using convert.py.
"""

import argparse
import json
import math
import statistics
from pathlib import Path


def _haversine_m(p1, p2) -> float:
    r = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _turn_angle(p1, p2, p3) -> float:
    a, b, c = _haversine_m(p2, p3), _haversine_m(p1, p3), _haversine_m(p1, p2)
    if a == 0 or c == 0:
        return 0.0
    return 180 - math.degrees(math.acos(max(-1.0, min(1.0, (a**2 + c**2 - b**2) / (2 * a * c)))))


BINS = [
    ("sharp",    lambda a: a > 75),
    ("medium",   lambda a: 50 < a <= 75),
    ("light",    lambda a: 25 < a <= 50),
    ("straight", lambda a: a <= 25),
]

PROFILE_FILE = Path(__file__).parent / "ride_profile.json"


def main():
    parser = argparse.ArgumentParser(description="Calibrate speed profile from real exported rides")
    parser.add_argument("rides", help="Folder of exported rides (output of export.py --type rides)")
    args = parser.parse_args()

    rides_dir = Path(args.rides)
    buckets: dict[str, list[float]] = {name: [] for name, _ in BINS}
    n_rides = 0

    for ride_dir in sorted(rides_dir.iterdir()):
        pts_f = ride_dir / "points.json"
        spd_f = ride_dir / "speeds.json"
        if not ride_dir.is_dir() or not pts_f.exists() or not spd_f.exists():
            continue

        points = json.loads(pts_f.read_text())["points"]
        speeds = json.loads(spd_f.read_text())["speeds"]
        n = min(len(points), len(speeds))
        if n < 3:
            continue

        n_rides += 1
        for i in range(1, n - 1):
            s = speeds[i]
            if s <= 0:
                continue
            angle = _turn_angle(points[i - 1], points[i], points[i + 1])
            for name, matches in BINS:
                if matches(angle):
                    buckets[name].append(s)
                    break

    if n_rides == 0:
        print(f"No rides with points.json + speeds.json found in {rides_dir}")
        return

    profile = {}
    print(f"Calibrated from {n_rides} rides:\n")
    print(f"  {'Category':<10}  {'mean km/h':>10}  {'stdev km/h':>11}  {'samples':>8}")
    print(f"  {'-'*46}")
    for name, _ in BINS:
        vals = buckets[name]
        if not vals:
            continue
        mean  = statistics.mean(vals)
        stdev = statistics.stdev(vals) if len(vals) > 1 else 1.0
        profile[name] = {"mean": mean, "stdev": stdev}
        print(f"  {name:<10}  {mean * 3.6:>10.1f}  {stdev * 3.6:>11.1f}  {len(vals):>8}")

    PROFILE_FILE.write_text(json.dumps(profile, indent=2))
    print(f"\nSaved to {PROFILE_FILE}")


if __name__ == "__main__":
    main()
