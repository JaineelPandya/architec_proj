from typing import Literal, Optional

from pydantic import BaseModel, Field


class SebiCircularRequest(BaseModel):
    search: Optional[str] = Field(
        default="",
        description="Search by title, keywords, or entity name (optional).",
    )
    from_date: str = Field(
        description="Start date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    to_date: str = Field(
        description="End date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    department: Optional[Literal[
        "", "All Department",
        "Alternative Investment Fund and Foreign Portfolio Investors Department",
        "Corporation Finance Department",
        "Department Economic and Policy Analysis",
        "Department of Debt and Hybrid Securities",
        "Enforcement Department - 1",
        "Information Technology Department",
        "Investment Management Department",
        "Market Intermediaries Regulation and Supervision Department",
        "Market Regulation Department",
        "Office of Investor Assistance and Education",
    ]] = Field(
        default="",
        description=(
            "SEBI department filter by name (optional). See GET /sebi/info for "
            "valid values. Omit or blank for all departments."
        ),
        examples=[
            "All Department",
            "Market Regulation Department",
            "Investment Management Department",
        ],
    )
    intermediary: Optional[Literal[
        "", "All Intermediary",
        "Banker to an Issue",
        "Debentures Trustee",
        "Credit Rating Agency - CRA",
        "KYC (Know Your Client) Registration Agency registered with SEBI",
        "Merchant Bankers",
        "Registrars to an issue and share Transfer Agents",
        "Underwriters",
        "Registered Alternative Investment Funds",
        "Registered Custodians",
        "Registered Foreign Venture Capital Investors",
        "Registered Mutual Funds",
        "Registered Portfolio Managers",
        "Registered Stock Brokers in Currency Derivative Segment",
        "Registered Stock Brokers in Equity Derivative Segment",
        "Registered Stock Brokers in equity segment",
        "Registered Venture Capital Funds",
        "Self-Certified Syndicate Banks under the direct ASBA facility (equity issuances)",
        "Deemed FPIs (Erstwhile Sub-Accounts)",
        "FPIs / Deemed FPIs (Erstwhile FIIs/QFIs)",
    ]] = Field(
        default="",
        description=(
            "SEBI intermediary filter by name (optional). See GET /sebi/info for "
            "valid values. Omit or blank for all intermediaries."
        ),
        examples=[
            "All Intermediary",
            "Registered Mutual Funds",
            "Merchant Bankers",
        ],
    )
    via_proxy: bool = Field(
        default=True,
        description=(
            "Route through the ScrapeOps proxy. Default false — mirrors SAT's "
            "via_proxy default, since routing through the proxy can interfere "
            "with SEBI's own request validation."
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
                    "search": "mutual fund",
                    "from_date": "2026-01-01",
                    "to_date": "2026-03-31",
                    "department": "Market Regulation Department",
                    "intermediary": "Registered Mutual Funds",
                },
            ]
        }
    }


class SebiMasterCircularRequest(BaseModel):
    from_date: str = Field(
        description="Start date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    to_date: str = Field(
        description="End date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    department: Optional[Literal[
        "", "All Department",
        "Alternative Investment Fund and Foreign Portfolio Investors Department",
        "Corporation Finance Department",
        "Department Economic and Policy Analysis",
        "Department of Debt and Hybrid Securities",
        "Enforcement Department - 1",
        "Integrated Surveillance Department",
        "Investment Management Department",
        "Market Intermediaries Regulation and Supervision Department",
        "Market Regulation Department",
        "Office of Investor Assistance and Education",
    ]] = Field(
        default="",
        description=(
            "SEBI department filter by name (optional). See GET /sebi/info for "
            "valid values. Omit or blank for all departments."
        ),
        examples=[
            "All Department",
            "Market Regulation Department",
            "Integrated Surveillance Department",
        ],
    )
    via_proxy: bool = Field(
        default=True,
        description=(
            "Route through the ScrapeOps proxy. Default false — mirrors SAT's "
            "via_proxy default, since routing through the proxy can interfere "
            "with SEBI's own request validation."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "from_date": "01-01-2020",
                    "to_date": "30-06-2026",
                    "department": "All Department",
                },
                {
                    "from_date": "2024-01-01",
                    "to_date": "2026-06-30",
                    "department": "Market Regulation Department",
                },
            ]
        }
    }
