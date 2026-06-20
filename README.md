# TS Assistant

TS Assistant is a companion app for the **Target Scheduler** plugin in
[NINA](https://nighttime-imaging.eu/). It gives you a fast, visual way to look at
your imaging projects and to plan new ones — on a real, draggable sky map instead
of NINA's nested tree editor.

You point it at your Target Scheduler database and it shows every project and
target plotted on the sky, over your choice of survey imagery. You can frame a
single target or a multi-panel mosaic against your telescope and camera's actual
field of view, then build a new project — targets, mosaic panels and exposure
plans — and save it, ready to import into NINA.

By default, your existing database is never touched: new projects are written to a
separate copy that you import into NINA yourself, so there's no risk to your real
data. If you'd rather have changes applied directly, there's an optional **live
mode** that reads and writes your real database (always taking a backup first).
See [Operating modes](#operating-modes-staging-vs-live).

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
  plans, then save them for import into NINA.
- **Reusable exposure plan templates** so you can apply a favourite filter/exposure
  recipe to a new target in one click.

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (for the backend).
- **Node.js** (for the frontend).
- A NINA **Target Scheduler** database (`schedulerdb.sqlite`). On the NINA machine
  this is usually at `%localappdata%\NINA\SchedulerPlugin\schedulerdb.sqlite`.

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

## Operating modes: staging vs live

TS Assistant runs in one of two modes. The mode is chosen when you start the
backend — there is no switch inside the app — so decide up front which one you
want.

### Staging mode (default)

This is what you get if you start the backend normally. TS Assistant reads from a
private snapshot of your database and saves new projects into a **separate copy**
at `data/export/schedulerdb-export.sqlite`. This copy is a full, NINA-importable
clone of your database with your new project added — **your real database is never
touched.**

The trade-off is that nothing you create takes effect automatically. To actually
use a project you built, you put that copy back in place yourself — close NINA,
then import the export file (or swap it in for your `schedulerdb.sqlite`). Until
you do, anything you save shows up only for the current session and disappears when
you reload the page.

### Live mode

Start the backend with the `TS_ASSISTANT_ALLOW_LIVE_WRITE=1` environment variable
to enable live mode:

```bash
cd backend
TS_ASSISTANT_ALLOW_LIVE_WRITE=1 uv run uvicorn app.main:app --host 0.0.0.0 --port 8008
```

In live mode TS Assistant reads and writes your **real** Target Scheduler database
directly. Projects you create take effect immediately and persist across reloads —
there's no separate file to import. Every write still takes an automatic backup
first, and TS Assistant refuses to write while NINA has the database open (you'll
get a clear "database busy" message instead). Live mode is off by default and is
meant to be turned on deliberately.

### Telling them apart

A banner across the top of the page always shows the current mode:

- **Staging mode** — a neutral banner: *"Staging mode — changes save to a separate
  copy; import it into NINA to use them."*
- **Live mode** — a red banner: *"● PRODUCTION — changes write directly to your
  live Target Scheduler database."*
- If live mode is requested but can't be used (for example, no database was found),
  a warning banner explains what's wrong.

## Using TS Assistant

The top bar holds the sky-view options; the left sidebar holds your equipment,
exposure plan templates, the project builder, and the list of existing projects.

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

### Saving a project

When the project has a name, at least one target, and at least one exposure plan,
the **Save to database** button becomes available. Saving writes the project, with
every mosaic expanded into individual panel targets, and where it goes depends on
your [operating mode](#operating-modes-staging-vs-live):

- **Staging mode (default):** the project is written to the separate copy at
  `data/export/schedulerdb-export.sqlite`. Import that file into NINA (or put it
  back in place of your `schedulerdb.sqlite`) to use the project.
- **Live mode:** the project is written straight into your real database and is
  ready to use immediately.

Either way, the save is additive — it only adds new rows — and an automatic backup
is taken first.

## Configuration

| Setting | What it does |
|---|---|
| `TS_ASSISTANT_DB` | Full path to your Target Scheduler database. If unset, the first database file in `sample_database/` is used. |
| `TS_ASSISTANT_ALLOW_LIVE_WRITE` | Set to `1` when starting the backend to enable [live mode](#operating-modes-staging-vs-live) (writes directly to your real database). Unset/`0` keeps the default, safe staging mode. |
| `VITE_API_BASE` | Set this for the frontend only if the backend runs on a different host/port than the default (`http://<same-host>:8008`). |
| Backend port | Passed to `uvicorn` with `--port` (default `8008`). |
| Frontend port | The dev server defaults to `5173`. |

## Good to know

- **In staging mode, saved projects aren't applied automatically.** A project you
  save in the default staging mode appears in the app for the current session, but
  it lives in the export copy — it won't reappear after a reload until you import
  it into NINA. (In [live mode](#operating-modes-staging-vs-live) saved projects
  persist immediately.)
- **This is a local, single-user tool.** It has no login and is meant to run on
  your own machine or local network. Don't expose it to the public internet.
- **Editing existing projects and templates isn't supported yet** — you can create
  new ones, and reference or duplicate existing templates, but not change or delete
  the ones already in your database.

## For developers

- The backend is FastAPI; interactive API docs are at
  `http://localhost:8008/docs` while it's running.
- Run the backend tests with `cd backend && uv run pytest`.
- `SCHEMA.md` is a generated reference of the Target Scheduler database schema
  (regenerate with `uv run python -m app.db.introspect`).
- The named-object overlay ships with a prebuilt catalog, so it works offline;
  `scripts/gen_named_objects.py` can regenerate it from public catalogs if you
  want to extend it.
