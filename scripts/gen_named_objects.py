#!/usr/bin/env python3
"""Generate the bundled named-object catalog for the sky-view overlay (bead cia).

Pulls from authoritative online catalogs (so coordinates/sizes are accurate, not
hand-typed) and writes frontend/src/sky/skyObjects.generated.json:

  - Messier (complete, 110)      via OpenNGC (mattiaverga/OpenNGC, J2000 + sizes + names)
  - Caldwell (Patrick Moore)      via OpenNGC (Caldwell numbers tagged in Identifiers)
  - IC highlights (size-filtered) via OpenNGC
  - Sharpless Sh2 HII regions     via VizieR VII/20  (size-filtered)
  - Large supernova remnants      via VizieR VII/284 (Green 2019, size-filtered)
  - A few famous NGC-only showpieces (featured) so they aren't lost

Run from anywhere (needs outbound HTTPS to GitHub raw + CDS VizieR):
    python3 scripts/gen_named_objects.py

Re-run to refresh the bundled data; commit the resulting JSON.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "frontend", "src", "sky", "skyObjects.generated.json")

OPENNGC_URL = (
    "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/"
    "database_files/NGC.csv"
)
# Non-NGC Messier (M45 Pleiades, M40, ...) live here, same column layout.
OPENNGC_ADDENDUM_URL = (
    "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/"
    "database_files/addendum.csv"
)
SH2_URL = (
    "https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=VII/20"
    "&-out=_RAJ2000,_DEJ2000,Sh2,Diam&-out.max=unlimited"
)
SNR_URL = (
    "https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=VII/284"
    "&-out=_RAJ2000,_DEJ2000,SNR,MajDiam,Names&-out.max=unlimited"
)

# Inclusion thresholds (major-axis arcmin) — keep the bundle to recognisable,
# on-sky-meaningful objects; runtime zoom-culling declutters further.
IC_MIN = 10.0
SH2_MIN = 30.0
SNR_MIN = 20.0

# Common names for the most famous Sharpless regions (nicer labels). Each entry
# is verified against SIMBAD's cross-identifications — Sh2 numbers do NOT line up
# with nearby nicknames by intuition (e.g. the Pelican is IC 5070 / part of
# Sh2-117, NOT Sh2-118, which is an anonymous HII region).
SH2_NAMES = {
    275: "Rosette Nebula",     # NAME Rosette Nebula
    117: "North America Nebula",  # = NGC 7000
    190: "Heart Nebula",       # = IC 1805
    199: "Soul Nebula",         # = IC 1848
    220: "California Nebula",    # = NGC 1499
    125: "Cocoon Nebula",       # associated to IC 5146
    155: "Cave Nebula",         # = Caldwell 9
    142: "Wizard Nebula",       # NGC 7380 region
    101: "Tulip Nebula",
    129: "Flying Bat Nebula",   # surrounds the Squid Nebula (Ou4)
    240: "Spaghetti Nebula",    # optical Simeis 147 / SNR G180.0-1.7
}

# Famous NGC-only showpieces that aren't Messier/IC/Sh2/SNR — preserved so the
# overlay keeps the recognisable targets it had. (J2000 deg, major axis arcmin.)
FEATURED_NGC = [
    ("NGC 7000", "North America Nebula", 314.75, 44.52, 120, "nebula"),
    ("NGC 2024", "Flame Nebula", 85.43, -1.85, 30, "nebula"),
    ("NGC 891", "", 35.64, 42.35, 14, "galaxy"),
    ("NGC 253", "Sculptor Galaxy", 11.9, -25.29, 27, "galaxy"),
    ("NGC 4565", "Needle Galaxy", 189.09, 25.99, 16, "galaxy"),
    ("NGC 5128", "Centaurus A", 201.36, -43.02, 26, "galaxy"),
    ("NGC 6888", "Crescent Nebula", 303.0, 38.35, 18, "nebula"),
    ("NGC 869", "Double Cluster", 35.0, 57.13, 60, "cluster"),
    ("NGC 7293", "Helix Nebula", 337.41, -20.84, 16, "planetary"),
    ("NGC 281", "Pacman Nebula", 13.2, 56.62, 35, "nebula"),
]

# Messier objects not cleanly in OpenNGC (star clouds / contested ids), to reach
# a complete 110. Only used to backfill numbers still missing after the CSVs.
MESSIER_EXTRA = {
    24: ("Sagittarius Star Cloud", 274.2, -18.55, 90.0, "cluster"),
    102: ("Spindle Galaxy", 226.623, 55.763, 6.5, "galaxy"),
}

# Catalog priority for de-duplication (lower wins when two entries coincide).
# Messier first, then Caldwell (the user-requested highlight list), then the
# survey catalogs; featured NGC ranks just above SNR so a coincident Caldwell
# entry is preferred but unique NGC showpieces (e.g. the Flame) still survive.
PRIORITY = {"M": 0, "C": 1, "IC": 2, "Sh2": 3, "NGC": 4, "SNR": 5}

# Matches a Caldwell designation token in OpenNGC's Identifiers column ("C 020").
CALDWELL_RE = re.compile(r"^C\s*0*([0-9]+)$")


def caldwell_num(identifiers: str) -> int | None:
    for tok in (identifiers or "").split(","):
        m = CALDWELL_RE.match(tok.strip())
        if m:
            return int(m.group(1))
    return None


def pretty_id(name: str) -> str:
    """OpenNGC compact name -> spaced catalog id: NGC0891 -> 'NGC 891'."""
    for p in ("NGC", "IC"):
        if name.startswith(p):
            num = name[len(p):].lstrip("0") or "0"
            return f"{p} {num}"
    return name


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ts-assistant-gen"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", "replace")


def hms_to_deg(s: str) -> float | None:
    s = s.strip().replace(":", " ")
    parts = s.split()
    if len(parts) < 2:
        return None
    h, m = float(parts[0]), float(parts[1])
    sec = float(parts[2]) if len(parts) > 2 else 0.0
    return (h + m / 60 + sec / 3600) * 15.0


def dms_to_deg(s: str) -> float | None:
    s = s.strip().replace(":", " ")
    sign = -1.0 if s.lstrip().startswith("-") else 1.0
    parts = s.replace("+", "").replace("-", "").split()
    if len(parts) < 2:
        return None
    d, m = float(parts[0]), float(parts[1])
    sec = float(parts[2]) if len(parts) > 2 else 0.0
    return sign * (d + m / 60 + sec / 3600)


def kind_from_type(t: str) -> str:
    t = t.strip()
    if t in ("G", "GPair", "GTrpl", "GGroup"):
        return "galaxy"
    if t == "PN":
        return "planetary"
    if t == "SNR":
        return "supernova"
    if t in ("OCl", "GCl", "Cl"):
        return "cluster"
    return "nebula"  # Neb, RfN, EmN, HII, Cl+N, DrkN, ...


def parse_tsv(text: str, ncols: int) -> list[list[str]]:
    """VizieR asu-tsv: skip comments + the 3 header lines (names/units/dashes)."""
    rows: list[list[str]] = []
    seen_dashes = False
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if line.startswith("-") and "----" in line:
            seen_dashes = True
            continue
        if not seen_dashes:
            continue  # still in the name/unit header block
        if len(cells) >= ncols:
            rows.append(cells)
    return rows


BAD_TYPES = ("Dup", "NonEx", "Other", "*", "**", "*Ass", "GxyCl")


def openngc_objects() -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(fetch(OPENNGC_URL)), delimiter=";"))
    rows += list(csv.DictReader(io.StringIO(fetch(OPENNGC_ADDENDUM_URL)), delimiter=";"))
    out: list[dict] = []
    seen_m: set[int] = set()
    seen_c: set[int] = set()
    for row in rows:
        name = (row.get("Name") or "").strip()
        typ = (row.get("Type") or "").strip()
        ra = hms_to_deg(row.get("RA") or "")
        dec = dms_to_deg(row.get("Dec") or "")
        if ra is None or dec is None:
            continue
        try:
            maj = float(row.get("MajAx") or "")
        except ValueError:
            maj = 0.0
        common = (row.get("Common names") or "").split(",")[0].strip()
        kind = kind_from_type(typ)
        mnum = (row.get("M") or "").strip()
        cnum = caldwell_num(row.get("Identifiers") or "")

        if mnum and typ != "Dup":
            # Messier: always include (even the asterism/double-star oddities),
            # except the "Dup" rows (e.g. M102 listed as a duplicate of M101).
            n = int(mnum)
            if n not in seen_m:
                seen_m.add(n)
                out.append(
                    dict(id=f"M{n}", name=common, ra=ra, dec=dec,
                         sizeArcmin=round(maj or 5.0, 2), kind=kind, catalog="M")
                )
            continue  # Caldwell excludes Messier; nothing else to add

        if cnum and cnum not in seen_c and typ not in BAD_TYPES:
            # Caldwell objects are all NGC/IC; keep the C-number as the id and
            # fall back to the underlying catalog name when there's no common one.
            seen_c.add(cnum)
            out.append(
                dict(id=f"C{cnum}", name=common or pretty_id(name), ra=ra, dec=dec,
                     sizeArcmin=round(maj or 5.0, 2), kind=kind, catalog="C")
            )
        elif name.startswith("IC") and maj >= IC_MIN and typ not in BAD_TYPES:
            num = name[2:].lstrip("0") or name[2:]
            out.append(
                dict(id=f"IC {num}", name=common, ra=ra, dec=dec,
                     sizeArcmin=round(maj, 2), kind=kind, catalog="IC")
            )

    # Backfill any Messier number still missing (star clouds / contested ids).
    for n, (nm, ra, dec, sz, kind) in MESSIER_EXTRA.items():
        if n not in seen_m:
            seen_m.add(n)
            out.append(
                dict(id=f"M{n}", name=nm, ra=ra, dec=dec,
                     sizeArcmin=sz, kind=kind, catalog="M")
            )
    return out


def sharpless() -> list[dict]:
    rows = parse_tsv(fetch(SH2_URL), 4)
    out: list[dict] = []
    for ra_s, dec_s, sh2, diam_s in (r[:4] for r in rows):
        try:
            ra, dec, diam = float(ra_s), float(dec_s), float(diam_s)
        except ValueError:
            continue
        if diam < SH2_MIN:
            continue
        n = int(sh2)
        out.append(
            dict(
                id=f"Sh2-{n}", name=SH2_NAMES.get(n, ""), ra=ra, dec=dec,
                sizeArcmin=round(diam, 2), kind="nebula", catalog="Sh2",
            )
        )
    return out


def snrs() -> list[dict]:
    rows = parse_tsv(fetch(SNR_URL), 5)
    out: list[dict] = []
    for cells in rows:
        ra_s, dec_s, gname, maj_s = cells[0], cells[1], cells[2], cells[3]
        names = cells[4].strip() if len(cells) > 4 else ""
        try:
            ra, dec, maj = float(ra_s), float(dec_s), float(maj_s)
        except ValueError:
            continue
        if maj < SNR_MIN:
            continue
        # Prefer a common name as the id; fall back to the catalogue G-name.
        ident = names if names else f"SNR {gname.strip()}"
        out.append(
            dict(
                id=ident, name="", ra=ra, dec=dec,
                sizeArcmin=round(maj, 2), kind="supernova", catalog="SNR",
            )
        )
    return out


def featured_ngc() -> list[dict]:
    return [
        dict(id=i, name=n, ra=ra, dec=dec, sizeArcmin=sz, kind=k, catalog="NGC")
        for (i, n, ra, dec, sz, k) in FEATURED_NGC
    ]


def angular_sep(a: dict, b: dict) -> float:
    ra1, dec1, ra2, dec2 = (
        math.radians(a["ra"]), math.radians(a["dec"]),
        math.radians(b["ra"]), math.radians(b["dec"]),
    )
    d = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, d))))


def dedup(objs: list[dict]) -> list[dict]:
    """Drop a lower-priority object whose centre coincides (<3') with a kept one."""
    objs = sorted(objs, key=lambda o: PRIORITY[o["catalog"]])
    kept: list[dict] = []
    for o in objs:
        if any(angular_sep(o, k) < 0.05 for k in kept):
            continue
        kept.append(o)
    return kept


def main() -> None:
    groups = {
        "featured NGC": featured_ngc(),
        "OpenNGC (M/C/IC)": openngc_objects(),
        "Sharpless": sharpless(),
        "SNR": snrs(),
    }
    for label, g in groups.items():
        print(f"  {label:14s}: {len(g)}")
    merged = dedup([o for g in groups.values() for o in g])

    by_cat: dict[str, int] = {}
    for o in merged:
        by_cat[o["catalog"]] = by_cat.get(o["catalog"], 0) + 1
    merged.sort(key=lambda o: (PRIORITY[o["catalog"]], o["id"]))

    with open(OUT, "w") as f:
        json.dump(merged, f, indent=0, separators=(",", ":"))
        f.write("\n")
    print(f"\nWrote {len(merged)} objects to {OUT}")
    print("  by catalog:", by_cat)


if __name__ == "__main__":
    main()
