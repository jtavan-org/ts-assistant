# TS Assistant

TS Assistant is a companion app for the **Target Scheduler** plugin in
[NINA](https://nighttime-imaging.eu/). It gives you a fast, visual way to look at
your imaging projects and to plan new ones — on a real, draggable sky map instead
of NINA's nested tree editor.

You point it at your Target Scheduler database and it shows every project and
target plotted on the sky, over your choice of survey imagery. You can frame a
single target or a multi-panel mosaic against your telescope and camera's actual
field of view, then build a new project — targets, mosaic panels and exposure
plans — or edit an existing draft, and save it straight back to your database.

TS Assistant works on your database **in place**, and takes a fresh backup before
every change (kept in `data/backups/`), so your data is protected and any change is
easy to roll back. It also refuses to write while NINA has the database open, so the
two never collide. See [Your data and backups](#your-data-and-backups).

## Features

- **Visual sky map** of all your projects and targets, powered by
  [Aladin Lite](https://aladin.cds.unistra.fr/AladinLite/). Click any target to
  centre on it.
- **Multiple sky surveys**, including DSS2 colour, Mellinger wide-field, and the
  **NSNS** narrowband surveys (Hα, [OIII], [SII] and colour composites) familiar
  from Stellarium.
- **Named-object overlay** that labels and circles several hundred well-known
  deep-sky objects (Messier, Caldwell, IC, Sharpless nebulae, large supernova
  remnants and more), sized to their real extent.
- **Field-of-view preview** from your own equipment: enter your camera and optics
  and TS Assistant draws your true frame on the sky.
- **Mosaic planning**: lay out an N×M grid of panels with adjustable overlap and
  rotation, or just drag a box over a region and let it work out the panels.
- **Project builder**: create projects with one or more targets and exposure
  plans — or **edit an existing draft project** — and save them straight to your
  database.
- **Per-profile scoping**: choose your NINA profile in the top bar and everything
  below — projects, templates, equipment — is scoped to it.
- **Reusable exposure plan templates** so you can apply a favourite filter/exposure
  recipe to a new target in one click.

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (for the backend).
- **Node.js** (for the frontend).
- A NINA **Target Scheduler** database (`schedulerdb.sqlite`). On the NINA machine
  this is usually at `%localappdata%\NINA\SchedulerPlugin\schedulerdb.sqlite`.

Prefer not to install Python and Node? See [Run with Docker](#run-with-docker-optional).

## Installation & running

TS Assistant has two parts that run side by side: a backend (the API) and a
frontend (the web interface). Start both.

### 1. Point it at your database

Copy your `schedulerdb.sqlite` into the `sample_database/` folder, **or** set an
environment variable to its full path:

```bash
export TS_ASSISTANT_DB=/path/to/schedulerdb.sqlite
```

The app still starts without a database — the interface just shows "no database
loaded" until you provide one.

### 2. Start the backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8008 --reload
```

This serves the API on port **8008**, reachable from other machines on your
network.

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Then open **<http://localhost:5173>** (or `http://<server-ip>:5173` from another
computer on your network). The interface finds the backend automatically on the
same host.

## Run with Docker (optional)

If you'd rather not install Python, uv, and Node, you can run everything with
[Docker](https://docs.docker.com/get-docker/) instead. This is completely
optional — the steps above work just as well.

1. Copy the example settings and edit them:

   ```bash
   cp .env.example .env
   ```

   At a minimum, set `TS_DB_DIR` to the folder that contains your
   `schedulerdb.sqlite` (or drop the database into `sample_database/` and leave
   the default).

2. Start it:

   ```bash
   docker compose up --build
   ```

3. Open **<http://localhost:5173>** (or `http://<server-ip>:5173` from another
   computer on your network).

A few things worth knowing:

- **Both ports are published** (5173 for the interface, 8008 for the API), because
  your browser talks to the API directly. Keep the backend on 8008 unless you know
  what you're changing.
- **Your database folder is mounted in** and is read and written in place (a backup
  is taken before every change).
- **Backups** live in `./data` next to the project, so they survive restarts and are
  easy to grab if you ever need to restore one.
- The container needs to write your database and its backups, so it runs as root
  inside the container. That's normal for a local tool you run yourself.

## Your data and backups

TS Assistant reads and writes one database — the one you point it at — **in place**.
Anything you save or edit takes effect immediately and is there when you reload.

Your safety net is automatic backups: **before every change, TS Assistant takes a
consistent backup** of your database into `data/backups/` (older ones are pruned, but
recent backups and everything from the last couple of weeks are kept). If you ever
need to undo something by hand, those are full copies you can restore.

A couple of built-in guards keep things safe:

- TS Assistant **won't write while NINA has the database open** — you'll get a clear
  "database busy" message instead, so the two never step on each other. Close or pause
  NINA's scheduler and try again.
- A small banner across the top shows **which database file you're working on** and a
  reminder that a backup is taken before every change.

## Using TS Assistant

The top bar holds the profile picker and sky-view options; the left sidebar holds
your equipment, exposure plan templates, the project builder, and the list of
existing projects.

### Choosing a profile

NINA's Target Scheduler is organised by **profile**, and so is TS Assistant. Use the
**NINA Profile** picker in the top bar to choose the active profile — the projects,
exposure templates, plan templates, and equipment shown below are all scoped to it,
and anything you create is added to it. The picker shows a short profile id by
default; click the ✎ button to give it a friendlier name.

### Choosing imagery

Use the **Survey** picker to switch the background sky imagery. DSS2 colour is the
all-sky default. The NSNS narrowband surveys only cover the northern sky (roughly
declination −20° and above) — outside that range they appear blank, so use DSS2 or
Mellinger for southern targets.

### Showing your field of view

In the **Equipment** panel, create a profile for your rig: pixel size, sensor
width and height, focal length, and an optional reducer/Barlow factor. TS
Assistant calculates your image scale and field of view and draws your frame on
the sky. Use the **FOV boxes** toggle in the top bar to show or hide it. Your
equipment profiles are saved between sessions.

### Labelling well-known objects

Turn on the **Named objects** toggle (top bar) to overlay labelled circles for
famous deep-sky objects. It's zoom-aware: zoomed out you see only the largest
objects; as you zoom in, smaller ones appear. This is handy for orienting yourself
and finding framing targets.

### Building a project

Open the **Project** panel and start a new project. A project holds one or more
**targets**, and each target is either a single pointing or a mosaic:

- Set the number of **panes** (columns × rows). 1×1 is a single frame; anything
  larger is a mosaic, with an adjustable **overlap** between panels.
- Set the **rotation** to match how you'll frame the field.
- Position the target on the sky in one of two ways:
  - **Place / move** — click or drag to set the centre. (Tip: use Aladin's
    built-in search bar to jump to an object by name first, or **Center here** to
    use the current view.)
  - **Cover area** — drag a box over the region you want to image and TS Assistant
    divides it into enough panels to cover it at your current field of view.

A readout shows the total area your framing covers.

### Adding exposure plans

Each project needs at least one **exposure plan**. A plan is a **Select Exposure
Template** dropdown (your existing Target Scheduler templates — the filter and
exposure time come from the template) plus the number of frames you want.

- To reuse a saved recipe, use **Apply plan template…** to fill in the plans in one
  click (you can still adjust them afterwards).
- If you need a template that doesn't exist yet, choose **＋ New template…** at the
  bottom of the dropdown. A small form lets you set the essentials (name, filter,
  gain, offset, binning, exposure), with an **Advanced options** section for the
  rest and a **Base on existing template** option to copy an existing one as a
  starting point.

### Reusing exposure plans (plan templates)

The **Exposure plan templates** panel lets you save a named bundle of exposure plans
— for example "LRGB Dark Nebula" or "SHO Bright Target" — and reuse it across projects
via the **Apply plan template…** option above. Exposure plan templates are a TS
Assistant convenience and are saved between sessions.

### Scheduler priorities (rule weights)

Each project carries the eight scoring **rule weights** that Target Scheduler uses to
decide what to image when. The **Rule weights** section of the builder shows them,
pre-filled with NINA's defaults; adjust any you like, or **Reset to defaults**. Leave
them untouched and you get exactly NINA's standard behaviour.

### Advanced project settings

The collapsible **Advanced settings** section exposes the rest of Target Scheduler's
project options — priority, minimum time, minimum/maximum altitude, custom horizon and
offset, meridian window, filter-switch frequency, dither-every, enable grader, smart
exposure order, and flats handling. All start at NINA's defaults (with **Reset to
defaults**), so you only touch what you care about. The same settings appear when you
edit a project, pre-filled with its current values.

### Exposure order (optional)

By default Target Scheduler captures filters using its normal cadence (driven by the
filter-switch frequency above). If you want an explicit sequence instead, use the
**Exposure order** section to build one step by step — **add an exposure** (pick which
filter) or **add a dither**, then reorder or remove steps. Leave it empty to keep NINA's
default behaviour. (Custom filter *cadence* itself stays auto-generated by NINA.)

### Saving a project

When the project has a name, at least one target, and at least one exposure plan, the
**Save to database** button becomes available. Saving writes the project — with every
mosaic expanded into individual panel targets — straight into your database, and it's
ready to use in NINA. A backup is taken first, and the new project appears in the list
and on the sky right away (and is still there after a reload).

### Editing an existing project

You can edit a project that's still a **draft and hasn't captured any frames yet** —
TS Assistant never touches projects that have started imaging. Such projects show an
**✎ Edit** button in the Projects list; click it to load the project into the builder,
change its name, targets, exposure plans, rule weights, advanced settings or exposure
order, and **Save changes** (or **Discard changes** to back out, or the trash to delete
it). The
view centres on the project when you open it, and clicking any target centres and
zooms to it.

Saved mosaics load as individual panels (the original grid isn't stored), so editing
is best for adjusting exposure plans and targets rather than re-tiling a mosaic.

## Configuration

| Setting | What it does |
|---|---|
| `TS_ASSISTANT_DB` | Full path to your Target Scheduler database. If unset, the first database file in `sample_database/` is used. |
| `TS_ASSISTANT_BACKUP_KEEP_LAST` | How many recent backups to always keep (default `10`). |
| `TS_ASSISTANT_BACKUP_KEEP_DAYS` | Also keep every backup from the last this-many days (default `14`). |
| `VITE_API_BASE` | Set this for the frontend only if the backend runs on a different host/port than the default (`http://<same-host>:8008`). |
| Backend port | Passed to `uvicorn` with `--port` (default `8008`). |
| Frontend port | The dev server defaults to `5173`. |

## Good to know

- **Changes go straight to your database.** A backup is taken before every change
  (see [Your data and backups](#your-data-and-backups)), so it's all recoverable —
  but close or pause NINA first; TS Assistant won't write while NINA holds the
  database, and NINA won't see your changes until it reloads the project list.
- **This is a local, single-user tool.** It has no login and is meant to run on
  your own machine or local network. Don't expose it to the public internet.
- **Some editing is still limited.** You can create projects and exposure templates,
  and edit draft projects that haven't started imaging — but you can't yet change or
  delete existing exposure templates, or edit projects that have already captured
  frames.

## For developers

- The backend is FastAPI; interactive API docs are at
  `http://localhost:8008/docs` while it's running.
- Run the backend tests with `cd backend && uv run pytest`.
- `SCHEMA.md` is a generated reference of the Target Scheduler database schema
  (regenerate with `uv run python -m app.db.introspect`).
- The named-object overlay ships with a prebuilt catalog, so it works offline;
  `scripts/gen_named_objects.py` can regenerate it from public catalogs if you
  want to extend it.
