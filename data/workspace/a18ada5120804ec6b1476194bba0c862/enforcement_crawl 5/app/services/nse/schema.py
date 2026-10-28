from pydantic import BaseModel, Field


class NseCircularsRequest(BaseModel):
    from_date: str = Field(
        description="Start date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    to_date: str = Field(
        description="End date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    via_proxy: bool = Field(
        default=True,
        description=(
            "Route through the ScrapeOps proxy. Default false — NSE requires "
            "specific headers (User-Agent, Accept, Referer) and may reject "
            "requests arriving through proxy aggregators."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "from_date": "01-01-2026",
                    "to_date": "30-06-2026",
                },
                {
                    "from_date": "2026-01-01",
                    "to_date": "2026-03-31",
                    "via_proxy": False,
                },
            ]
        }
    }
