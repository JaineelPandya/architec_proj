from typing import Literal

from pydantic import BaseModel, Field


class NseRegulationsRequest(BaseModel):
    entity: Literal["", "NSE", "NSE Clearing", "NSE International Exchange Ltd", "NSE International Clearing Ltd"] = Field(
        default="",
        description=(
            "Optional filter. One of: NSE | NSE Clearing | "
            "NSE International Exchange Ltd | NSE International Clearing Ltd "
            "(case-insensitive). Leave blank to return documents for all entities."
        ),
    )
    via_proxy: bool = Field(
        default=True,
        description=(
            "Route through the ScrapeOps proxy. Default false — this is a plain "
            "static content page, but direct fetches are the primary tested path."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {},
                {"entity": "NSE"},
                {"entity": "NSE International Clearing Ltd"},
            ]
        }
    }
