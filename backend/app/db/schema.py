"""Canonical Target Scheduler (NINA) database schema.

Captured verbatim from a real ``schedulerdb.sqlite`` so that a freshly created
database is byte-for-byte identical to one NINA produces (same ``sqlite_master.sql``
for every table). This is the public ``nina.plugin.assistant`` schema — structure
only, no user data.

Notes that the round-trip fidelity test (mh3.1) pins down as the real contract:

* The Target Scheduler plugin manages its schema with hand-written SQL migrations
  (``ALTER TABLE ... ADD COLUMN``), **not** EF Core. So there is **no**
  ``__EFMigrationsHistory`` table and no other EF metadata — the bead's
  "EFMigrationsHistory expectations" resolve to *"that table must not exist"*.
* No column uses ``AUTOINCREMENT``; each ``Id`` is an ``INTEGER`` rowid alias
  (``Id INTEGER NOT NULL, PRIMARY KEY(Id)``), so there is **no** ``sqlite_sequence``
  table either, and a plain INSERT lets SQLite assign the next Id.
* Table names are lower-case singular (``project``/``target``/``exposureplan`` …);
  identifier quoting is intentionally mixed (backticks and double-quotes) and is
  preserved exactly so the stored DDL matches NINA's.

Do not hand-edit the CREATE statements — regenerate from a real DB if NINA's
schema changes (see scripts/, mh3.1).
"""

from __future__ import annotations

import sqlite3

# Verbatim CREATE TABLE statements, in NINA's own order.
CREATE_STATEMENTS: list[str] = [
    'CREATE TABLE `project` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`profileId`\t\tTEXT NOT NULL,\r\n\t`name`\t\t\tTEXT NOT NULL,\r\n\t`description`\tTEXT,\r\n\t`state`\t\t\tINTEGER,\r\n\t`priority`\t\tINTEGER,\r\n\t`createdate`\tINTEGER,\r\n\t`activedate`\tINTEGER,\r\n\t`inactivedate`\tINTEGER,\r\n\t`minimumtime`\tINTEGER,\r\n\t`minimumaltitude`\tREAL,\r\n\t`usecustomhorizon`\tINTEGER,\r\n\t`horizonoffset`\tREAL,\r\n\t`meridianwindow`\tINTEGER,\r\n\t`filterswitchfrequency`\tINTEGER,\r\n\t`ditherevery`\tINTEGER,\r\n\t`enablegrader`\tINTEGER, isMosaic INTEGER NOT NULL DEFAULT 0, flatsHandling INTEGER NOT NULL DEFAULT 0, maximumAltitude REAL DEFAULT 0, smartexposureorder INTEGER DEFAULT 0, guid TEXT,\r\n\tPRIMARY KEY(`id`)\r\n)',
    'CREATE TABLE `target` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`name`\t\t\tTEXT NOT NULL,\r\n\t`active`\t\tINTEGER NOT NULL,\r\n\t`ra`\t\t\tREAL,\r\n\t`dec`\t\t\tREAL,\r\n\t`epochcode`\t\tINTEGER NOT NULL,\r\n\t`rotation`\t\tREAL,\r\n\t`roi`\t\t\tREAL,\r\n\t`projectid`\t\tINTEGER, unusedOEO TEXT, guid TEXT,\r\n\tFOREIGN KEY(`projectId`) REFERENCES `project`(`Id`),\r\n\tPRIMARY KEY(`id`)\r\n)',
    'CREATE TABLE `exposureplan` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`profileId`\t\tTEXT NOT NULL,\r\n\t`exposure`\t\tREAL NOT NULL,\r\n\t`desired`\t\tINTEGER,\r\n\t`acquired`\t\tINTEGER,\r\n\t`accepted`\t\tINTEGER,\r\n\t`targetid`\t\tINTEGER,\r\n\t`exposureTemplateId`\tINTEGER, enabled INTEGER DEFAULT 1, guid TEXT,\r\n\tFOREIGN KEY(`targetId`) REFERENCES `target`(`Id`),\r\n\tFOREIGN KEY(`exposureTemplateId`) REFERENCES `exposuretemplate`(`Id`),\r\n\tPRIMARY KEY(`Id`)\r\n)',
    'CREATE TABLE `exposuretemplate` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n    `profileId`\t\tTEXT NOT NULL,\r\n    `name`\t\t\tTEXT NOT NULL,\r\n    `filtername`\tTEXT NOT NULL,\r\n\t`gain`\t\t\tINTEGER,\r\n\t`offset`\t\tINTEGER,\r\n\t`bin`\t\t\tINTEGER,\r\n\t`readoutmode`\tINTEGER,\r\n\t`twilightlevel` INTEGER,\r\n\t`moonavoidanceenabled`\tINTEGER,\r\n\t`moonavoidanceseparation`\tREAL,\r\n\t`moonavoidancewidth`\tINTEGER,\r\n\t`maximumhumidity`\tREAL, defaultexposure REAL DEFAULT 60, moonrelaxscale REAL DEFAULT 0, moonrelaxmaxaltitude REAL DEFAULT 5, moonrelaxminaltitude REAL DEFAULT -15, moondownenabled INTEGER DEFAULT 0, ditherevery INTEGER DEFAULT -1, minutesOffset INTEGER DEFAULT 0, guid TEXT,\r\n\tPRIMARY KEY(`Id`)\r\n)',
    'CREATE TABLE `ruleweight` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`name`\t\t\tTEXT NOT NULL,\r\n    `weight`\t\tREAL NOT NULL,\r\n\t`projectid`\t\tINTEGER,\r\n\tFOREIGN KEY(`projectId`) REFERENCES `project`(`Id`),\r\n\tPRIMARY KEY(`Id`)\r\n)',
    'CREATE TABLE `acquiredimage` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`projectId`\t\tINTEGER NOT NULL,\r\n\t`targetId`\t\tINTEGER NOT NULL,\r\n\t`acquireddate`\tINTEGER,\r\n\t`filtername`\tTEXT NOT NULL,\r\n\t"gradingStatus"\t\tINTEGER NOT NULL,\r\n    `metadata`\t\tTEXT NOT NULL, rejectreason TEXT, profileId TEXT, exposureId INTEGER DEFAULT 0, guid TEXT,\r\n\tPRIMARY KEY(`Id`)\r\n)',
    'CREATE TABLE `imagedata` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`tag`\t\t\tTEXT,\r\n\t`imagedata`\t\tBLOB,\r\n\t`acquiredimageid`\tINTEGER, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0,\r\n\tFOREIGN KEY(`acquiredImageId`) REFERENCES `acquiredimage`(`Id`),\r\n\tPRIMARY KEY(`Id`)\r\n)',
    'CREATE TABLE `profilepreference` (\r\n\t`Id`\t\t\tINTEGER NOT NULL,\r\n\t`profileId`\t\tTEXT NOT NULL,\r\n\t`enableGradeRMS`\tINTEGER,\r\n\t`enableGradeStars`\tINTEGER,\r\n\t`enableGradeHFR`\tINTEGER,\r\n\t`maxGradingSampleSize`\t\tINTEGER,\r\n\t`rmsPixelThreshold`\t\t\tREAL,\r\n\t`detectedStarsSigmaFactor`\tREAL,\r\n\t`hfrSigmaFactor`\t\t\tREAL, acceptimprovement INTEGER DEFAULT 1, exposurethrottle REAL DEFAULT 125, parkonwait INTEGER DEFAULT 0, enableSmartPlanWindow INTEGER DEFAULT 1, enableSynchronization INTEGER DEFAULT 0, syncWaitTimeout INTEGER DEFAULT 300, syncActionTimeout INTEGER DEFAULT 300, syncSolveRotateTimeout INTEGER DEFAULT 300, enableMoveRejected INTEGER DEFAULT 0, enableGradeFWHM INTEGER DEFAULT 0, enableGradeEccentricity INTEGER DEFAULT 0, fwhmSigmaFactor INTEGER DEFAULT 4, eccentricitySigmaFactor INTEGER DEFAULT 4, enableDeleteAcquiredImagesWithTarget INTEGER DEFAULT 1, syncEventContainerTimeout INTEGER DEFAULT 300, delayGrading REAL DEFAULT 80, autoAcceptLevelHFR REAL DEFAULT 0, autoAcceptLevelFWHM REAL DEFAULT 0, autoAcceptLevelEccentricity REAL DEFAULT 0, enableSimulatedRun INTEGER DEFAULT 0, skipSimulatedWaits INTEGER  DEFAULT 1, skipSimulatedUpdates INTEGER DEFAULT 0, enableSlewCenter INTEGER DEFAULT 1, logLevel INTEGER DEFAULT 3, enableStopOnHumidity INTEGER DEFAULT 1, guid TEXT, enableProfileTargetCompletionReset INTEGER DEFAULT 0, enableAPI INTEGER DEFAULT 0, apiPort INTEGER DEFAULT 8188, apiPrettyPrint INTEGER DEFAULT 0,\r\n\tPRIMARY KEY(`id`)\r\n)',
    'CREATE TABLE `flathistory` (\r\n   `Id`        INTEGER NOT NULL,\r\n   `targetId`         INTEGER,\r\n   `lightSessionDate`   INTEGER,\r\n   `flatsTakenDate`   INTEGER,\r\n   `profileId`    TEXT NOT NULL,\r\n   `flatsType`    TEXT,\r\n   `filterName`    TEXT,\r\n   `gain`         INTEGER,\r\n   `offset`    INTEGER,\r\n   `bin`       INTEGER,\r\n   `readoutmode`  INTEGER,\r\n   `rotation`        REAL,\r\n   `roi`        REAL, lightSessionId INTEGER NOT NULL DEFAULT 0,\r\n   PRIMARY KEY(`id`)\r\n)',
    'CREATE TABLE "overrideexposureorderitem" (\r\n   "Id"\t\t\t\tINTEGER NOT NULL,\r\n   "targetid"\t\tINTEGER NOT NULL,\r\n   "order"\t\t\tINTEGER NOT NULL,\r\n   "action"\t\t\tINTEGER NOT NULL,\r\n   "referenceIdx"\tINTEGER,\r\n   PRIMARY KEY("Id")\r\n)',
    'CREATE TABLE "filtercadenceitem" (\r\n   "Id"\t\t\t\tINTEGER NOT NULL,\r\n   "targetid"\t\tINTEGER NOT NULL,\r\n   "order"\t\t\tINTEGER NOT NULL,\r\n   "next"\t\t\tINTEGER,\r\n   "action"\t\t\tINTEGER NOT NULL,\r\n   "referenceIdx"\tINTEGER,\r\n   PRIMARY KEY("Id")\r\n)',
]

EXPECTED_TABLES: frozenset[str] = frozenset({
    'project',
    'target',
    'exposureplan',
    'exposuretemplate',
    'ruleweight',
    'acquiredimage',
    'imagedata',
    'profilepreference',
    'flathistory',
    'overrideexposureorderitem',
    'filtercadenceitem',
})


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the full canonical Target Scheduler schema on a fresh connection."""
    for stmt in CREATE_STATEMENTS:
        conn.execute(stmt)


def canonical_sql_by_table() -> dict[str, str]:
    """Map table name -> its exact CREATE statement (for fidelity diffing)."""
    out: dict[str, str] = {}
    for stmt in CREATE_STATEMENTS:
        conn = sqlite3.connect(":memory:")
        conn.execute(stmt)
        name = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        out[name] = stmt
        conn.close()
    return out

