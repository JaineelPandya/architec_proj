from typing import Literal, Optional

from pydantic import BaseModel, Field


class BseCircularRequest(BaseModel):
    start_date: str = Field(
        description="Start date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    end_date: str = Field(
        description="End date (inclusive). Format: DD-MM-YYYY or YYYY-MM-DD",
    )
    segment: Optional[Literal[
        "", "BSEHITECH", "Commodity Derivatives", "Currency Derivatives", "Debt",
        "Derivatives", "EGR", "Equity", "General", "IA/RA",
        "Investment Advisers", "IRD", "Mutual Fund", "NDS", "Research Analysts",
        "SLB", "SME", "SSE", "Startups",
    ]] = Field(
        default="",
        description="BSE segment filter (optional).",
    )
    category: Optional[Literal[
        "", "Circulars Listed Companies", "Company related", "Compliance",
        "Corporate Actions", "Index", "Others", "Settlement/RMS", "Tenders",
        "Trading",
    ]] = Field(
        default="",
        description="BSE category filter (optional).",
    )
    department: Optional[Literal[
        "", "Bad Delivery Cell", "BSE Training Institute", "BTI",
        "Business Development", "Business Operations", "Clearing & Settlement",
        "Corporate Communications", "Corporate Relations", "Corporate Secreterial",
        "DAPM - General Administration", "Datawarehouse Regulatory", "Debt",
        "Derivatives", "DIS", "DOSS", "Economist Cell", "ED's Office",
        "Estate & Maintenance", "Exchange Compliance", "Finance & Accounts",
        "Fixed Income", "General Administration", "Helpdesk", "HR",
        "Human Resources", "ICC", "Index Cell", "Information Systems",
        "Information Systems (Projects)", "Information Technology", "Inspection",
        "Investors Services Cell", "IPF Secretariat", "ISMS", "IT-Networks",
        "IT-Operations", "Legal", "Listing Business Relationship",
        "Listing Compliance", "Listing Compliance-Monitoring", "Listing Operations",
        "Market Operations", "Marketing", "MD office", "Member Institution Sales",
        "Membership", "Membership Compliance", "Membership Services", "Personnel",
        "Post Trade", "Premises Management", "Regulatory Correspondence Dept.",
        "S&P BSE Index", "Secretarial", "Security", "SME", "Surveillance",
        "Tainted Shares", "Trading Development", "Trading Operations",
        "Unified Business Commercial",
    ]] = Field(
        default="",
        description="BSE department filter (optional).",
    )
    subject: Optional[str] = Field(
        default="",
        description="Free-text subject search (optional).",
    )
    via_proxy: bool = Field(
        default=True,
        description=(
            "Route through the ScrapeOps proxy. Default false — BSE's API "
            "blocks requests that arrive without matching Referer/Origin "
            "headers, which proxy aggregators may strip or alter."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "start_date": "01-06-2026",
                    "end_date": "30-06-2026",
                    "segment": "Equity",
                    "category": "Trading",
                    "department": "Listing Operations",
                },
                {
                    "start_date": "2026-01-01",
                    "end_date": "2026-03-31",
                    "segment": "Debt",
                    "category": "Corporate Actions",
                    "department": "Listing Operations",
                    "subject": "Suspension",
                },
                {
                    "start_date": "01-01-2021",
                    "end_date": "31-01-2025",
                },
            ]
        }
    }
