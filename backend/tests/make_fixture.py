"""Build a small, schema-faithful Target Scheduler database for testing.

Column names mirror the real Entity Framework schema (tcpalmer/nina.plugin.assistant)
so the reader/introspection is exercised exactly as it will be against a real DB.
RA is stored in HOURS and Dec in degrees, matching how Target Scheduler stores them
(verified empirically against a real database).

Usage:
    uv run python -m tests.make_fixture /tmp/fixture.sqlite
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DDL = """
CREATE TABLE project (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ProfileId TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    state INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 1,
    createDate INTEGER,
    activeDate INTEGER,
    inactiveDate INTEGER,
    isMosaic INTEGER NOT NULL DEFAULT 0,
    flatsHandling INTEGER NOT NULL DEFAULT 0,
    minimumTime INTEGER NOT NULL DEFAULT 30,
    minimumAltitude REAL NOT NULL DEFAULT 0,
    useCustomHorizon INTEGER NOT NULL DEFAULT 0,
    horizonOffset REAL NOT NULL DEFAULT 0,
    meridianWindow INTEGER NOT NULL DEFAULT 0,
    filterSwitchFrequency INTEGER NOT NULL DEFAULT 0,
    ditherEvery INTEGER NOT NULL DEFAULT 0,
    enableGrader INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE target (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    ra REAL NOT NULL,
    dec REAL NOT NULL,
    epochCode INTEGER NOT NULL DEFAULT 0,
    rotation REAL NOT NULL DEFAULT 0,
    roi REAL NOT NULL DEFAULT 100,
    overrideExposureOrder TEXT,
    ProjectId INTEGER NOT NULL,
    FOREIGN KEY (ProjectId) REFERENCES project (Id) ON DELETE CASCADE
);
CREATE TABLE exposuretemplate (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    profileId TEXT NOT NULL,
    name TEXT NOT NULL,
    filterName TEXT NOT NULL,
    defaultExposure REAL NOT NULL DEFAULT 300,
    gain INTEGER NOT NULL DEFAULT -1,
    offset INTEGER NOT NULL DEFAULT -1,
    bin INTEGER,
    readoutMode INTEGER NOT NULL DEFAULT -1,
    twilightlevel INTEGER NOT NULL DEFAULT 0,
    moonAvoidanceEnabled INTEGER NOT NULL DEFAULT 0,
    moonAvoidanceSeparation REAL NOT NULL DEFAULT 0,
    moonAvoidanceWidth INTEGER NOT NULL DEFAULT 0,
    moonRelaxScale REAL NOT NULL DEFAULT 0,
    moonRelaxMaxAltitude REAL NOT NULL DEFAULT 0,
    moonRelaxMinAltitude REAL NOT NULL DEFAULT 0,
    moonDownEnabled INTEGER NOT NULL DEFAULT 0,
    maximumHumidity REAL NOT NULL DEFAULT 0
);
CREATE TABLE exposureplan (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    profileId TEXT NOT NULL,
    exposure REAL NOT NULL DEFAULT -1,
    desired INTEGER NOT NULL DEFAULT 1,
    acquired INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    ExposureTemplateId INTEGER NOT NULL,
    TargetId INTEGER NOT NULL,
    FOREIGN KEY (ExposureTemplateId) REFERENCES exposuretemplate (Id),
    FOREIGN KEY (TargetId) REFERENCES target (Id) ON DELETE CASCADE
);
CREATE TABLE ruleweight (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    weight REAL NOT NULL,
    projectid INTEGER NOT NULL,
    FOREIGN KEY (projectid) REFERENCES project (Id) ON DELETE CASCADE
);
"""

# NINA's 8 scoring rules with their default weights (mirrors writer.DEFAULT_RULE_WEIGHTS).
FIXTURE_RULE_WEIGHTS = (
    ("Meridian Flip Penalty", 0.0),
    ("Meridian Window Priority", 75.0),
    ("Mosaic Completion", 0.0),
    ("Percent Complete", 50.0),
    ("Project Priority", 50.0),
    ("Setting Soonest", 50.0),
    ("Smart Exposure Order", 0.0),
    ("Target Switch Penalty", 67.0),
)


def _seed_rule_weights(conn, project_id: int) -> None:
    for name, weight in FIXTURE_RULE_WEIGHTS:
        conn.execute(
            "INSERT INTO ruleweight (name, weight, projectid) VALUES (?, ?, ?)",
            (name, weight, project_id),
        )

PROFILE = "11111111-1111-1111-1111-111111111111"


def build(path: Path) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(DDL)

    # Exposure templates: SHO narrowband set.
    templates = [("Ha", "Ha"), ("OIII", "OIII"), ("SII", "SII")]
    tmpl_ids = {}
    for name, filt in templates:
        cur = conn.execute(
            "INSERT INTO exposuretemplate (profileId, name, filterName, defaultExposure)"
            " VALUES (?, ?, ?, ?)",
            (PROFILE, name, filt, 300.0),
        )
        tmpl_ids[filt] = cur.lastrowid

    # Project 1: a 2x2 mosaic of the North America / Pelican region (state=active).
    cur = conn.execute(
        "INSERT INTO project (ProfileId, name, description, state, priority, isMosaic)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (PROFILE, "NA/Pelican Mosaic", "2x2 SHO mosaic", 1, 2, 1),
    )
    p1 = cur.lastrowid
    # Four panels around RA ~20.9h (313.5 deg), Dec ~44 deg. RA in HOURS.
    panels = [
        ("Panel 1-1", 20.80, 45.0),
        ("Panel 1-2", 21.00, 45.0),
        ("Panel 2-1", 20.80, 43.0),
        ("Panel 2-2", 21.00, 43.0),
    ]
    for name, ra, dec in panels:
        tcur = conn.execute(
            "INSERT INTO target (name, active, ra, dec, epochCode, rotation, roi, ProjectId)"
            " VALUES (?, 1, ?, ?, 0, 0, 100, ?)",
            (name, ra, dec, p1),
        )
        tid = tcur.lastrowid
        for filt in ("Ha", "OIII", "SII"):
            conn.execute(
                "INSERT INTO exposureplan (profileId, exposure, desired, acquired, accepted,"
                " ExposureTemplateId, TargetId) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (PROFILE, 300.0, 30, 12, 10, tmpl_ids[filt], tid),
            )
    _seed_rule_weights(conn, p1)

    # Project 2: a single target (state=draft) with a couple of non-default settings
    # so the reader's advanced-settings projection (psq) is exercised.
    cur = conn.execute(
        "INSERT INTO project (ProfileId, name, description, state, priority, isMosaic,"
        " minimumTime, enableGrader)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (PROFILE, "M31 Andromeda", "single frame", 0, 1, 0, 45, 0),
    )
    p2 = cur.lastrowid
    tcur = conn.execute(
        "INSERT INTO target (name, active, ra, dec, epochCode, rotation, roi, ProjectId)"
        " VALUES (?, 1, ?, ?, 0, ?, 100, ?)",
        # M31: RA 00h42m43s = 0.712313 hours (= 10.6847 deg); Dec +41.269 deg.
        ("M31", 0.712313, 41.269, 35.0, p2),
    )
    conn.execute(
        "INSERT INTO exposureplan (profileId, exposure, desired, ExposureTemplateId, TargetId)"
        " VALUES (?, ?, ?, ?, ?)",
        (PROFILE, 180.0, 60, tmpl_ids["Ha"], tcur.lastrowid),
    )
    _seed_rule_weights(conn, p2)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ts_fixture.sqlite")
    build(out)
    print(f"Wrote fixture: {out}")
