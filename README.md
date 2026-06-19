# TS Assistant

A companion app for NINA's **Target Scheduler** plugin that makes managing
projects, targets, and (eventually) mosaics far easier than the in-NINA tree
editor — with a real, draggable sky view powered by [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/)
and HiPS surveys (including the **NSNS** narrowband surveys you get in Stellarium).

This repo is at **v1 (read-only sky overview)**. See
[`docs/ROADMAP`](#roadmap) below and the design notes for what comes next.

## Architecture

- **backend/** — Python + FastAPI. Opens a *copy* of `schedulerdb.sqlite`
  read-only, introspects the real schema, and serves projects/targets and the
  HiPS survey catalog as JSON. The source database is never modified.
- **frontend/** — Vite + React + TypeScript. Aladin Lite sky view, survey
  switcher, and a project/target browser with click-to-center.

```
Target Scheduler db  ──copy──▶  backend (FastAPI, read-only)
                                      │  /api/projects, /api/surveys
                                      ▼
                          frontend (React + Aladin Lite)
```

## Quick start

1. **Provide a database.** Copy a Target Scheduler `schedulerdb.sqlite` into
   `sample_database/` (default location on the NINA box:
   `%localappdata%\NINA\SchedulerPlugin\schedulerdb.sqlite`). Or point at one
   explicitly with `TS_ASSISTANT_DB=/path/to/schedulerdb.sqlite`.

2. **Backend:** (binds `0.0.0.0:8008` so it's reachable on the LAN)
   ```bash
   cd backend
   uv sync
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8008 --reload
   ```
   Inspect the real schema at any time:
   ```bash
   uv run python -m app.db.introspect   # writes ../SCHEMA.md
   ```

3. **Frontend:** (also binds `0.0.0.0`)
   ```bash
   cd frontend
   npm install
   npm run dev          # http://localhost:5173 (or http://<server-ip>:5173)
   ```

Open <http://localhost:5173> (or `http://<server-ip>:5173` from another machine).
Pick a survey (try **NSNS Hα + continuum** for a northern target), and click a
target in the sidebar to center the sky on it. The frontend auto-targets the
backend at `http://<same-host>:8008`; override with `VITE_API_BASE` if needed.

## Safety model

The app operates on a **copy** of the database (snapshotted into `data/working/`)
and opens it in SQLite read-only mode. v1 contains no write paths at all. The
future export feature (create projects/targets) will be an explicit, validated
step that takes an automatic timestamped backup first.

## Tests

```bash
cd backend && uv run pytest      # reader/introspection against a fixture db
```

## Roadmap

- **v1 (now):** read-only — list projects/targets, project them on the sky dome,
  switch HiPS surveys.
- **P2:** equipment/FOV definition + draw/position/rotate a mosaic grid.
- **P3:** write path — create Project + panel Targets + ExposurePlans (backup +
  validation + transactional write).
- **P4:** exposure plan/template assignment UI, rule weights, polish.
