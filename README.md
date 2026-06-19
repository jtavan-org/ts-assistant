# TS Assistant

A companion app for NINA's **Target Scheduler** plugin that makes managing
projects, targets, and mosaics far easier than the in-NINA tree editor — with a
real, draggable sky view powered by [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/)
and HiPS surveys (including the **NSNS** narrowband surveys you get in
Stellarium).

You point it at a Target Scheduler database, see all your projects and targets
plotted on the sky, switch between survey imagery, and frame single pointings or
multi-panel mosaics against your rig's real field of view — all without touching
the original database.

> **Status:** read-only sky overview, equipment/FOV + mosaic framing, **and**
> project creation — you can now build a project and **save** it (exported to a
> staging Target Scheduler database you then import into NINA). Direct writes to
> your live database are intentionally not exposed. See
> [Known gaps & limitations](#known-gaps--limitations).

## What it does today

- **Browse your scheduler.** Lists every project and its targets (with nested
  exposure plans), read straight from a copy of your `schedulerdb.sqlite`.
- **Plot targets on the sky.** Click any target in the sidebar to center the
  Aladin Lite view on it.
- **Switch survey imagery.** DSS2 color (all-sky default), DSS2 NIR, Mellinger
  wide-field, and the full set of **NSNS DR0.2** narrowband composites
  (OHS, Hα, [OIII], [SII], RGB) plus the NSNS DR0.1 true-color layer.
- **Label sky highlights.** An optional **Named objects** overlay labels and
  circles several hundred well-known deep-sky objects (the Messier and Caldwell
  catalogs, IC highlights, Sharpless HII regions, large supernova remnants, and a
  few featured NGC showpieces) sized to their real angular extent. It's
  zoom-aware — a wide field shows only the largest objects; zoom in to reveal
  progressively smaller ones.
- **Define equipment / field of view.** Create rig profiles (pixel size, sensor
  dimensions, focal length, corrector/reducer factor); the app computes plate
  scale and FOV and overlays the FOV box on the sky.
- **Draft projects, single targets, and mosaics.** A **project** is the top-tier
  artifact and holds one or more **targets**. Each target has a **panes (N×M)**
  count: 1×1 is an individual pointing, anything larger is a mosaic. Per target
  you set panes, overlap (only when it's a mosaic), and rotation, position it on
  the sky, and read off the total coverage area. The panel grid is drawn live on
  the sky dome.
- **Create projects (save / export).** Give the project a NINA profile and
  exposure plans (filter + sub-exposure + frame count), then **Save** — each
  framed mosaic is expanded into per-pane targets and written as a Project +
  Targets + ExposurePlans. The write goes to a **staging** Target Scheduler
  database (`data/export/`) for you to import into NINA; it's additive-only,
  auto-backed-up, and undoable, and never touches your live database.

## Architecture

- **backend/** — Python + FastAPI. Reads a *copy* of `schedulerdb.sqlite`
  read-only, introspects the real schema, and serves projects/targets, the HiPS
  survey catalog, and equipment profiles as JSON. The source database is never
  modified; created projects are written to a separate **staging** database.
- **frontend/** — Vite + React + TypeScript. Aladin Lite sky view, survey
  switcher, named-object overlay, equipment panel, project/mosaic builder, and a
  project/target browser with click-to-center.

```
Target Scheduler db  ──copy(ro)──▶  backend (FastAPI)
                                      │  /api/projects, /api/surveys,
                                      │  /api/equipment, /api/schema, /api/export
                                      ▼
                          frontend (React + Aladin Lite)
                                      │  Save
                                      ▼
                          staging db (data/export/) ──import──▶ NINA
```

Equipment profiles are **app-local data** (stored in `data/equipment.json`),
*not* in your Target Scheduler database.

## Quick start

### 1. Provide a database

Copy a Target Scheduler `schedulerdb.sqlite` into `sample_database/` (default
location on the NINA box:
`%localappdata%\NINA\SchedulerPlugin\schedulerdb.sqlite`). Or point at one
explicitly:

```bash
export TS_ASSISTANT_DB=/path/to/schedulerdb.sqlite
```

If `TS_ASSISTANT_DB` is set it wins; otherwise the first `*.sqlite` / `*.sqlite3`
/ `*.db` file found in `sample_database/` is used. The app starts fine with **no**
database (the UI just shows "no database loaded") so you can wire it up first.

### 2. Backend

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/). Binds `0.0.0.0:8008`
so it's reachable on the LAN.

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8008 --reload
```

Inspect the real schema at any time (regenerates `../SCHEMA.md`):

```bash
uv run python -m app.db.introspect
```

### 3. Frontend

Requires Node.js. Also binds `0.0.0.0`.

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (or http://<server-ip>:5173)
```

Open <http://localhost:5173> (or `http://<server-ip>:5173` from another machine).
Pick a survey (try **NSNS Hα + continuum** for a northern target), and click a
target in the sidebar to center the sky on it. The frontend auto-targets the
backend at `http://<same-host>:8008`; override with `VITE_API_BASE` if the
backend lives elsewhere.

## Using the app

- **Survey** picker (top bar) switches the HiPS base layer. NSNS layers only
  cover Dec ≳ −20°, so DSS2 color is the all-sky default.
- **FOV boxes** toggle (top bar) shows/hides the rig field-of-view overlay.
- **Named objects** toggle (top bar, off by default) shows/hides the deep-sky
  highlight overlay. Intended flow: toggle it on, then zoom to your region —
  smaller objects appear as you zoom in.
- **Equipment** panel (sidebar): pick or create a rig. Edits show a live plate
  scale + FOV readout and update the on-sky FOV box. Profiles persist via the
  backend (`data/equipment.json`).
- **Project** panel (sidebar): start a new project draft (it begins with a single
  1×1 target at the view center), add more targets, and for each target set
  columns/rows (panes), overlap % (mosaics only), and rotation. Position a target
  using either placement mode:
  - **Place / move** — click/drag a center point (resolve an object by name first
    with Aladin's built-in search bar, or use **Center here** to snap to the
    current view center).
  - **Cover area** — drag a box over the region you want imaged; the target's
    panes auto-divide to **fully cover** that area at the current rig FOV and
    overlap, adopting the dragged box's center, size, and orientation (if the
    Aladin view is rotated, the grid tilts to match). cols/rows stay editable
    afterward.

  The coverage readout shows the overall framed area. To save the project, fill in
  the **NINA profile** field (type an id, or pick one of the suggestions drawn
  from your existing projects) and add at least one **Exposure plan** (the ＋
  button adds a row: filter name, sub-exposure in seconds, and desired frame
  count). The **Save to database** button enables once the project has a name, a
  profile, a target, and an exposure plan; saving expands each mosaic into
  per-pane targets and writes them to a staging database via `POST /api/export`.
  The saved project appears in the list and on the sky for the session — note it
  is **session-local and disappears on reload** (reads come from your source
  database copy, not the staging export; see [Known gaps](#known-gaps--limitations)).
- **Projects** panel (sidebar): the read-only list of existing projects/targets
  from the database; click a target to center on it.

## HTTP API

All endpoints are under `/api`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + whether a source DB was found. |
| GET | `/projects` | Projects with nested targets and exposure plans. |
| GET | `/targets` | Flat list of all targets (convenience for overlays). |
| GET | `/schema` | Table names + row counts of the working copy. |
| GET | `/surveys` | HiPS survey catalog offered by the sky view. |
| GET | `/equipment` | Equipment profiles (with computed FOV). |
| POST | `/equipment` | Create a profile (server mints the id). |
| PUT | `/equipment/{id}` | Update a profile. |
| DELETE | `/equipment/{id}` | Delete a profile. |
| POST | `/export` | Additively write a project + targets + exposure plans to a **staging** copy (`data/export/`), after validation and an automatic backup. Returns an operation summary (`operation_id`, `backup_path`, `project_id`, `target_ids`, counts). |
| POST | `/export/{operation_id}/undo` | Remove exactly the rows a previous export added (refused if any have captured progress). |

The Project panel's **Save to database** button posts to `POST /api/export`.
Writes target a safe staging copy under `data/export/`, never the live Target
Scheduler database; live writes are env-gated (`TS_ASSISTANT_ALLOW_LIVE_WRITE`)
and not exposed over HTTP.

Read-model notes: RA is stored in **hours** in the Target Scheduler DB and
converted to degrees (×15) by the reader; Dec is degrees. Project `state` and
target `epochCode` integer enums are decoded to labels. The reader is tolerant of
missing columns / schema drift across Target Scheduler versions.

## Safety model

Reads operate on a **copy** of the database (snapshotted into `data/working/`),
opened in SQLite read-only URI mode (`?mode=ro`). The working copy is
re-snapshotted from the source on each read, so changes you make in NINA show up
on the next request. The reader never writes to your source database.

The export path (mh3.4, `POST /api/export`) writes only to a separate **staging
copy** under `data/export/` — never your live Target Scheduler database — and is
deliberately **additive-only**: it inserts a new project with its targets and
exposure plans and never modifies or deletes existing rows. Every export is
preceded by an automatic timestamped backup and is undoable
(`POST /api/export/{operation_id}/undo`). Direct writes to the live database exist
in the writer but are gated behind the `TS_ASSISTANT_ALLOW_LIVE_WRITE` environment
variable and are not exposed via the API or UI.

> CORS is open to all origins by design — this is a local, single-user tool with
> no auth or cookies, intended to be reachable across your LAN.

## Tests

```bash
cd backend && uv run pytest      # reader/introspection + FOV math against fixtures
```

The backend tests cover the read layer (against a schema-faithful fixture
database built in `tests/make_fixture.py`) and the FOV/plate-scale computation.
The frontend has no automated test suite yet; the `frontend/*.mjs` files are
local Playwright verification scratch (gitignored).

## Maintaining the named-object catalog

The **Named objects** overlay is driven by a committed, bundled catalog
(`frontend/src/sky/skyObjects.generated.json`) so it works offline and renders
deterministically — **end users never need to regenerate it.**

If you want to extend or refresh the catalog, regenerate it from authoritative
sources:

```bash
python3 scripts/gen_named_objects.py    # stdlib only; needs outbound HTTPS
```

The generator pulls coordinates and angular sizes from **OpenNGC** (Messier /
Caldwell / IC) and **VizieR/CDS** (`VII/20` Sharpless, `VII/284` Green supernova
remnants) — nothing is hand-typed — and writes the JSON the frontend imports.
Tunable knobs at the top of the script control what's included: the `IC_MIN` /
`SH2_MIN` / `SNR_MIN` minimum-size thresholds, plus `MESSIER_EXTRA` and
`FEATURED_NGC` for explicit additions. (Adjusting the thresholds changes the
object count, so the catalog size is intentionally not pinned in these docs.)

## Known gaps & limitations

These are the things that are **not** done or that may surprise you. Tracked in
more detail in the `.beads/` issue tracker (`br ready`, `br list --status=open`).

- **Saved projects are session-local until imported.** A project you Save shows up
  in the list and on the sky for the current session, but **disappears on reload**:
  it's written to the staging export database (`data/export/`), while the read path
  loads only your source database copy. To make it permanent you import the staging
  database into NINA. Persisting created projects across reloads is tracked as bead
  `2ij`.
- **Save targets a staging database, not your live one.** Saving produces a staging
  Target Scheduler DB you import into NINA, rather than writing into the database
  the app read from. Direct live writes exist in the backend but are gated behind
  `TS_ASSISTANT_ALLOW_LIVE_WRITE` and not exposed in the API or UI.
- **No exposure-plan / template / rule-weight editing.** Exposure plans are shown
  read-only (nested under targets); there is no UI to create or edit exposure
  templates, assign plans, or tune rule weights yet.
- **Target search is Aladin's, not a custom UI.** You resolve objects by name via
  Aladin Lite's built-in search bar (plus click/center to position); there's no
  TS-Assistant-specific search/resolver box integrated into the project builder
  yet.
- **NSNS survey coverage.** All NSNS narrowband layers only cover Dec ≳ −20°;
  outside that band they render empty. Use DSS2/Mellinger for the southern sky.
- **Equipment store is app-local and single-user.** Profiles live in
  `data/equipment.json` with no locking; concurrent editors can clobber each
  other.
- **Working copy refreshes every request.** Each API read re-copies the source
  database into `data/working/`. This is fine for typical scheduler DBs but is
  unoptimized for very large databases.

## Roadmap

- **Read-only sky overview** *(done)* — list projects/targets, project them on
  the sky dome, switch HiPS surveys.
- **P2 — FOV & mosaic framing** *(done)* — equipment/FOV definition +
  draw/position/rotate a mosaic grid, plus coverage-area framing (drag a region →
  auto-divide into rig-FOV panels).
- **P3 — Write / export path** *(done)* — schema round-trip gating test (`mh3.1`),
  transactional additive writer with backup + undo (`mh3.2`), pre-write validation
  (`mh3.3`), the export API (`mh3.4`), and the create-Project-from-mosaic Save UI
  (`mh3.5`) are all merged. Save → staging export, end-to-end.
- **P4 — Exposure plans, templates & polish** *(next)* — exposure plan/template
  assignment UI, rule weights, target search/resolver, persisting created projects
  across reloads (`2ij`), UX cleanup.

---

*This README is maintained by an agent (TurquoiseSpring). The companion schema
reference is auto-generated in [`SCHEMA.md`](./SCHEMA.md). Agent/issue workflow
notes are in [`AGENTS.md`](./AGENTS.md).*
