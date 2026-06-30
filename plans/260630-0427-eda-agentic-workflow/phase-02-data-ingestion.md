---
phase: 2
title: "Data Ingestion"
status: pending
priority: P1
effort: "1d"
dependencies: [1]
---

# Phase 2: Data Ingestion

## Overview
Accept CSV/Parquet uploads via UI, persist to temp storage, and provide a loader that returns a pandas DataFrame given a `dataset_ref`. All other backend phases consume data through this loader — they never touch the filesystem directly.

## Requirements
- Functional: upload handler validates **magic bytes** (not extension/Content-Type) + size, writes to a temp store, returns opaque `dataset_ref`; `load_dataframe(ref)` returns a DataFrame with robust parsing (encoding/delimiter sniffing for CSV).
- Non-functional: compressed-upload byte cap (default 200MB) AND a **post-load row×col / memory ceiling** (decompression-bomb guard — a few-MB parquet can expand to GBs); refs are non-guessable AND **validated as well-formed uuid on every consume**; temp files are **leased by active `run_id`, never deleted while a non-terminal run references them**.

## Red Team fixes applied
- **Path traversal (High):** `path_for(ref)` must `uuid.UUID(ref)`-validate then assert `os.path.commonpath([realpath, temp_dir]) == temp_dir`. Applies to dataset refs AND report refs (Phase 9 `/report/{ref}`).
- **Decompression bomb / magic bytes (High):** sniff magic bytes; read parquet metadata first and reject if `num_rows * num_cols` exceeds a bound; stream CSV with a hard byte counter.
- **TTL race (Critical):** cleanup must lease/refcount by `run_id` — never age-delete a ref an in-flight or interrupt-paused run still needs. Reset TTL on access; sweep only terminal/abandoned runs.

## Architecture
New package `server/app/ingestion/`:
- `storage.py` — `save_upload(file) -> ref`, `path_for(ref)` (uuid-validate + commonpath containment), `lease(ref, run_id)` / `release(ref, run_id)`, `cleanup_expired()` (skips leased refs). Refs = uuid4; files under a configurable temp dir.
- `loader.py` — `load_dataframe(ref) -> pd.DataFrame`. CSV: try utf-8 then latin-1, sniff delimiter via `csv.Sniffer`, surface parse errors as a typed `IngestionError`. Parquet: `pd.read_parquet`.
- `models.py` — `DatasetMeta(ref, filename, rows, cols, size_bytes, dtypes)`.

The upload route itself is added in Phase 9 (route ownership). Phase 2 exposes the functions the route will call. To keep Phase 2 testable standalone, include a thin FastAPI `APIRouter` in `ingestion/router.py` that Phase 9 mounts — but Phase 9 owns `routes/eda.py`. Ingestion router only handles `/eda/upload`.

NOTE: `pandas` + `pyarrow` are seeded into `requirements.txt` by Phase 1 (which owns that file). Phase 2 does NOT edit requirements.txt.

## Related Code Files
- Create: `server/app/ingestion/__init__.py`, `storage.py`, `loader.py`, `models.py`, `router.py`
- Create: `server/tests/ingestion/test_loader.py`

## Implementation Steps
1. `storage.py`: uuid ref, save under temp dir, TTL cleanup helper.
2. `loader.py`: CSV encoding/delimiter sniffing + Parquet; raise `IngestionError` on failure.
3. `models.py`: `DatasetMeta` + `describe(ref) -> DatasetMeta`.
4. `router.py`: `POST /eda/upload` (multipart) → validate → save → return `{dataset_ref, meta}`.
5. Tests: load a fixture CSV with a weird delimiter + a Parquet; assert shape + dtypes; assert oversize rejected.

## Success Criteria
- [ ] Upload a 1MB CSV → get a ref → `load_dataframe(ref)` returns correct shape.
- [ ] Delimiter/encoding sniffing handles `;`-delimited + latin-1 fixtures.
- [ ] Oversize + wrong-type uploads rejected with clear error.
- [ ] Tests pass under conda `research`.

## Risk Assessment
Risk: pandas parse heuristics misread exotic CSVs. Mitigation: surface raw parse error to user via `IngestionError`; let the Framing phase note encoding caveats. Risk: temp-dir growth. Mitigation: TTL cleanup invoked on upload.
