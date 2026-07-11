# API fixtures

This directory contains no fabricated API snapshots. The measured Seoul raw
fixture was captured on 2026-07-11. Kakao remains pending because the app's
Map/Local service returned a disabled-service 403. Run
`python scripts/verify_apis.py` with issued credentials to create missing
service outputs:

- `citydata_sample.json`
- `kakao_ce7_sample.json`
- `citydata_sample.summary.json`
- `kakao_ce7_sample.summary.json`

Official static master inputs are downloaded separately with:

```bash
rtk uv run python scripts/download_hotspot_master.py --file all
```

- `seoul_hotspots_master.xlsx`: 121 codes, names, and categories
- `seoul_hotspot_areas.zip`: matching WGS84 Shapefile polygons

The downloader validates attachment metadata and ZIP signatures, writes
atomically, and refuses to overwrite reviewed originals.

The raw response is written before provisional schema validation, and the
script refuses to overwrite any existing output. A schema mismatch leaves the
raw JSON in place and creates a sibling `.validation_error.txt` report for
review. Tests never invoke live APIs. Before measured fixtures exist, tests only
exercise request construction, secret handling, overwrite protection, and raw
evidence preservation with in-memory HTTP responses. Upstream schema contract
tests are added only after reviewed files exist in this directory.
