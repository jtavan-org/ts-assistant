"""HiPS survey catalog served to the frontend.

These are the surveys the sky view offers. NSNS (Northern Sky Narrowband Survey,
the Stellarium favourite) and DSS are all HiPS, so Aladin Lite loads them by URL.
NSNS only covers Dec >= ~-20 deg, hence DSS2 color as the all-sky default.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Survey(BaseModel):
    id: str
    label: str
    # Aladin accepts either a HiPS base URL or a registered CDS id.
    url_or_id: str
    is_default: bool = False
    note: str | None = None


SURVEYS: list[Survey] = [
    Survey(
        id="dss2-color",
        label="DSS2 Color (all-sky)",
        # Pin the CDS/alasky mirror directly: the "CDS/P/DSS2/color" registry id
        # can resolve to an IRSA mirror that lacks CORS headers.
        url_or_id="https://alasky.cds.unistra.fr/DSS/DSSColor",
        is_default=True,
        note="Full-sky default base layer.",
    ),
    Survey(
        id="dss2-nir",
        label="DSS2 NIR",
        url_or_id="CDS/P/DSS2/NIR",
    ),
    # NSNS HiPS base URLs are under /nebulae3/dr0_1/<layer> (the hips_service_url
    # in each survey's properties). simg.de sends Access-Control-Allow-Origin: *,
    # so Aladin Lite can load them directly. Dec ≳ -20° coverage only.
    Survey(
        id="nsns-halpha8",
        label="NSNS Hα (8-bit)",
        url_or_id="https://simg.de/nebulae3/dr0_1/halpha8",
        note="Northern Sky Narrowband Survey; Dec ≳ -20° only.",
    ),
    Survey(
        id="nsns-hbr8",
        label="NSNS Hα + continuum (color)",
        url_or_id="https://simg.de/nebulae3/dr0_1/hbr8",
        note="Northern Sky Narrowband Survey; Dec ≳ -20° only.",
    ),
    Survey(
        id="nsns-tc8",
        label="NSNS True Color",
        url_or_id="https://simg.de/nebulae3/dr0_1/tc8",
        note="Northern Sky Narrowband Survey; Dec ≳ -20° only.",
    ),
    Survey(
        id="mellinger",
        label="Mellinger Color (wide field)",
        url_or_id="CDS/P/Mellinger/color",
    ),
]


@router.get("/surveys", response_model=list[Survey])
def get_surveys() -> list[Survey]:
    return SURVEYS
