#!/usr/bin/env python3
"""
Train a Gaussian HMM from real exported rides and save it as ride_profile.json.
Run this once before using convert.py.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from hmmlearn import hmm

FEATURES = ["angle", "steepness", "speed"]
DECODE_FEATURES = ["angle", "steepness"]
SLOPE_WINDOW_M = 60.0
SLOPE_CLIP = 30.0


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


def _compute_slopes(points: list, altitudes: list, window_m: float = SLOPE_WINDOW_M) -> list[float]:
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
        slopes[i] = max(-SLOPE_CLIP, min(SLOPE_CLIP, s))
    return slopes


def _get_model_observations(points: list, speeds: list, altitudes: list) -> list[list[float]]:
    n = min(len(points), len(speeds))
    slopes = _compute_slopes(points, altitudes) if altitudes else [0.0] * len(points)
    obs = []
    for i in range(1, n - 1):
        s = speeds[i]
        if s <= 0:
            continue
        obs.append([_turn_angle(points[i - 1], points[i], points[i + 1]), abs(slopes[i]), s])
    return obs


PROFILE_FILE = Path(__file__).parent / "ride_profile.json"


def main():
    parser = argparse.ArgumentParser(description="Train a HMM from real exported rides")
    parser.add_argument("rides", help="Folder of exported rides (output of export.py --type rides)")
    parser.add_argument("--states", type=int, default=6, help="Number of hidden riding regimes")
    parser.add_argument("--iter", type=int, default=100, help="Max Baum-Welch iterations")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument(
        "--cap-percentile",
        type=float,
        default=99.5,
        help="Speed-cap percentile of the training data (robust to outliers; 100 = raw max)",
    )
    args = parser.parse_args()

    rides_dir = Path(args.rides)
    sequences, lengths, n_rides = [], [], 0

    for ride_dir in sorted(rides_dir.iterdir()):
        pts_f = ride_dir / "points.json"
        spd_f = ride_dir / "speeds.json"
        alt_f = ride_dir / "altitudes.json"
        if not ride_dir.is_dir() or not pts_f.exists() or not spd_f.exists():
            continue

        points = json.loads(pts_f.read_text())["points"]
        speeds = json.loads(spd_f.read_text())["speeds"]
        altitudes = json.loads(alt_f.read_text())["altitudes"] if alt_f.exists() else []
        obs = _get_model_observations(points, speeds, altitudes)
        if len(obs) < 3:
            continue

        sequences.extend(obs)
        lengths.append(len(obs))
        n_rides += 1

    if n_rides == 0:
        print(f"No rides with points.json + speeds.json found in {rides_dir}")
        return

    X = np.asarray(sequences, dtype=float)
    model = hmm.GaussianHMM(
        n_components=args.states,
        covariance_type="diag",
        n_iter=args.iter,
        random_state=args.seed,
    )
    model.fit(X, lengths)

    # Order states by mean speed so the profile is human-readable and stable.
    order = np.argsort(model.means_[:, FEATURES.index("speed")])
    states = model.predict(X, lengths)
    counts = np.bincount(states, minlength=args.states)
    ai, si, vi = (FEATURES.index(f) for f in ("angle", "steepness", "speed"))

    profile = {
        "type": "gaussian_hmm",
        "n_states": args.states,
        "features": FEATURES,
        "decode_features": DECODE_FEATURES,
        "slope_window_m": SLOPE_WINDOW_M,
        "slope_clip": SLOPE_CLIP,
        "speed_max": float(np.percentile(X[:, vi], args.cap_percentile)),
        "startprob": model.startprob_[order].tolist(),
        "transmat": model.transmat_[np.ix_(order, order)].tolist(),
        "angle_mean": model.means_[order, ai].tolist(),
        "angle_var": model.covars_[order, ai, ai].tolist(),
        "steepness_mean": model.means_[order, si].tolist(),
        "steepness_var": model.covars_[order, si, si].tolist(),
        "speed_mean": model.means_[order, vi].tolist(),
        "speed_var": model.covars_[order, vi, vi].tolist(),
    }

    PROFILE_FILE.write_text(json.dumps(profile, indent=2))

    print(f"Trained {args.states}-state HMM from {n_rides} rides "
          f"({len(sequences)} observations), converged={model.monitor_.converged}")
    print(f"Speed cap (p{args.cap_percentile:g} of training data): "
          f"{profile['speed_max'] * 3.6:.1f} km/h\n")
    print(f"  {'State':>5}  {'angle':>7}  {'steep':>7}  {'speed km/h':>10}  {'stay':>5}  {'samples':>8}")
    print(f"  {'-' * 56}")
    for new_i, old_i in enumerate(order):
        print(f"  {new_i:>5}  {model.means_[old_i, ai]:>6.1f}°  "
              f"{model.means_[old_i, si]:>6.1f}%  "
              f"{model.means_[old_i, vi] * 3.6:>10.1f}  "
              f"{model.transmat_[old_i, old_i]:>5.2f}  {counts[old_i]:>8}")
    print(f"\nSaved to {PROFILE_FILE}")


if __name__ == "__main__":
    main()
