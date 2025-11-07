# Calimoto Importer

Export rides and planned routes from [Calimoto](https://calimoto.com), convert planned routes into completed rides, and import them back.

On my trip up to norh cape Calimoto lost the track of one the longer rides probably due to a hardware failure, as the phone overheated multiple times on that trip.
This annoyed me so much that i investigated the api and implemented with these scripts to take a planned route, add elevation data, interpolate the ride and import it into calimoto.

## Requirements

```
pip install -r requirements.txt
```

## Setup

Copy `.env.example` to `.env` and fill in your credentials:

```
CALIMOTO_USERNAME=your@email.com
CALIMOTO_PASSWORD=yourpassword

CALIMOTO_APP_ID=<APP ID>
CALIMOTO_JS_KEY=<JS KEY>
CALIMOTO_BASE_URL=https://parse-server.prod.calimoto.com/parse
CALIMOTO_CLIENT_VER=<VERSION>
```

Inspect the traffic to Calimoto and collect the required values.

## Scripts

### `export.py` — Export rides or planned routes

```bash
python export.py --out nordkapp --from <YYYY-MM-DD> --to <YYYY-MM-DD>
python export.py --out all_rides

python export.py --out my_routes --type planned
```

Each entry is saved as a subfolder `<name>-<id>/` containing `meta.json`, data files, and a GPX file.

---

### `calibrate.py` — Calibrate speed profile from real rides

Run once before using `convert.py` to analyse exported rides and to derive approximate speed values per turn angle.

```bash
python calibrate.py all_rides/
```

Writes `ride_profile.json`, which is required by `convert.py` to generate a somewhat realistic drive.

---

### `convert.py` — Convert a planned route to a completed ride

Fetches elevation data, simulates speeds and timestamps based on the calibrated profile.

```bash
python convert.py <PLANNED ROUTE> --out <OUTPUT FOLDER> --date 2021-07-15 --name <NAME>
```

Output folder contains `points.json`, `altitudes.json`, `speeds.json`, `dates.json`, `tour.json`.

Elevation data is cached in `elevation_cache.json` and reused across runs.

---

### `import.py` — Import a converted ride into Calimoto

```bash
python import.py <CONVERTED TOUR>
```

Uploads all data files, generates a map preview image, and creates the track record in your Calimoto account.


