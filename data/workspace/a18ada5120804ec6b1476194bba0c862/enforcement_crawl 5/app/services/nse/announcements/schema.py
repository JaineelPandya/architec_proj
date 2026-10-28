"""
app/services/nse/announcements/schema.py
=========================================
Pydantic models for the NSE corporate announcements endpoint.

Ported from nse_corporate_crawler/app/nse/schemas.py.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.nse.announcements.constants import (
    DEFAULT_SEGMENT,
    SUPPORTED_SEGMENTS,
)


class AnnouncementQuery(BaseModel):
    """
    Query parameters for NSE corporate announcements.

    All fields are optional except `index` which defaults to
    ``equities``. Dates are accepted as DD-MM-YYYY or YYYY-MM-DD.
    """

    model_config = ConfigDict(extra="forbid")

    index: str = Field(
        default=DEFAULT_SEGMENT,
        description=(
            f"NSE market segment. One of: {sorted(SUPPORTED_SEGMENTS)}"
        ),
        examples=["equities"],
    )

    symbol: str | None = Field(
        default=None,
        description=(
            "NSE trading symbol. Leave empty for all companies."
        ),
        examples=["RELIANCE"],
    )

    from_date: date | None = Field(
        default=None,
        description="Start date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
        examples=["10-09-2026"],
    )

    to_date: date | None = Field(
        default=None,
        description="End date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
        examples=["17-09-2026"],
    )

    fno: bool = Field(
        default=False,
        description="Restrict results to F&O (Futures & Options) stocks.",
        examples=[False],
    )

    via_proxy: bool = Field(
        default=True,
        description="Route through the ScrapeOps proxy. Default true.",
    )

    @field_validator("from_date", "to_date", mode="before")
    @classmethod
    def parse_flexible_date(cls, v: Any) -> date | None:
        """Parse DD-MM-YYYY, YYYY-MM-DD, or DD/MM/YYYY into a date object."""
        if v is None or v == "":
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            v = v.strip()
            for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    return datetime.strptime(v, fmt).date()
                except ValueError:
                    continue
            raise ValueError(
                f"Invalid date format '{v}'. Expected DD-MM-YYYY or YYYY-MM-DD."
            )
        raise ValueError(f"Invalid date type: {type(v).__name__}")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "index": "equities",
                    "from_date": "10-09-2026",
                    "to_date": "17-09-2026",
                },
                {
                    "index": "equities",
                    "symbol": "RELIANCE",
                    "from_date": "01-09-2026",
                    "to_date": "17-09-2026",
                    "fno": False,
                },
            ]
        }
    }


class Announcement(BaseModel):
    """Normalised representation of a single NSE corporate announcement."""

    model_config = ConfigDict(extra="forbid")

    # Identity
    announcement_id: str | None = None

    # Company
    segment: str | None = None
    symbol: str | None = None
    company_name: str | None = None

    # Announcement body
    subject: str | None = None
    details: str | None = None

    # Attachment
    attachment_url: str | None = None

    # Timing (DD-MM-YYYY format)
    broadcast_date: str | None = Field(
        default=None,
        description="Announcement date in DD-MM-YYYY format",
    )
    broadcast_datetime: str | None = Field(
        default=None,
        description="Announcement datetime in DD-MM-YYYY HH:MM:SS format",
    )


class AnnouncementResponse(BaseModel):
    """Envelope returned by the /announcement endpoint."""

    source: str = "NSE"
    document_type: str = "corporate_announcement"

    requested_url: str

    filters: dict[str, Any]

    count: int

    records: list[Announcement]
