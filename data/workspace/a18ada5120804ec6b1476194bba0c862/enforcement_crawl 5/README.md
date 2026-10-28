# Enforcement Crawler

**Enforcement Crawler** is a stateless web-scraping API service for regulatory data — SEBI, BSE, NSE and SAT (Securities Appellate Tribunal). It has **no database**; every endpoint scrapes the upstream site live (with a short in-process TTL cache) and returns normalized JSON.

---

## Table of Contents

- [Environment Setup](#environment-setup)
- [Dependencies](#dependencies)
- [Running Locally](#running-locally)
- [Automated Smoke Test (recommended)](#automated-smoke-test-recommended)
- [Authentication](#authentication)
- [Module Overview](#module-overview)
- [Health Checks](#health-checks)
- [SAT — Securities Appellate Tribunal](#sat--securities-appellate-tribunal)
- [NSE — Circulars](#nse--circulars)
- [SEBI — Circulars & Master Circulars](#sebi--circulars--master-circulars)
- [BSE — Notices & Circulars](#bse--notices--circulars)
- [Quick Smoke Test (all modules)](#quick-smoke-test-all-modules)

---

## Environment Setup

Copy the sample env file and fill in your keys:

```bash
cp sample_env.txt .env
```

| Variable | Required | Description |
|---|---|---|
| `SCRAPEOPS_API_KEY` | No | ScrapeOps proxy key. Only used when a request sets `via_proxy: true`. |
| `SCRAPEOPS_URL` | No | Reference URL for the ScrapeOps proxy account. |
| `SEARCH_API_KEY` / `SEARCH_API_URL` | No | Unused by SAT/NSE/SEBI/BSE — reserved for other modules. |
| `SERVICE_TOKEN` | No | Bearer token required on every `/api/enforcement_crawler/*` call. Leave empty to disable auth (dev only). |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`). |
| `LOG_JSON` | No | Set `true` for structured JSON logs. |

> `.env` is git-ignored. Never commit real credentials — API keys are also auto-redacted from logs.

---

## Dependencies

All dependencies are pinned in `requirements.txt`. Install them with:

```bash
pip install -r requirements.txt
```

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | 0.120.0 | API framework |
| `uvicorn` | 0.38.0 | ASGI server |
| `pydantic` | 2.12.3 | Request/response schemas |
| `pydantic-settings` | 2.11.0 | Environment config |
| `python-dotenv` | 1.2.1 | `.env` file loading |
| `httpx` | 0.28.1 | Async HTTP client |
| `beautifulsoup4` | 4.14.2 | HTML parsing |
| `python-dateutil` | 2.9.0.post0 | Flexible date parsing |
| `curl_cffi` | 0.14.0 | TLS fingerprint-spoofing HTTP client (proxy support) |
| `pypdfium2` | 5.12.1 | Primary in-memory PDF text extraction (SEBI circulars) |
| `pdfplumber` | 0.11.10 | Fallback PDF text extraction when `pypdfium2` yields no result |

> **Note on PDF libraries:** `pypdfium2` is the primary extractor (fast, native). `pdfplumber` is the fallback and kicks in only when `pypdfium2` returns empty or very short text, or when no SEBI circular number is found. Both operate strictly in-memory — no PDFs are ever written to disk.

---

## Running Locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Interactive docs: `http://localhost:8001/docs`

> Every `curl` example further down in this doc talks to `http://localhost:8001` and needs a server running via the command above (or `scripts/test_apis.sh` below, which starts one for you). A "connection refused" / empty response on any `curl` almost always means no server is listening on that host:port — start one first.

---

## Automated Smoke Test (recommended)

Instead of copy-pasting `curl` blocks by hand, run the executable script — it starts a server automatically if one isn't already up on `HOST:PORT` (and stops it on exit if it did), then hits every module's health check plus one live endpoint call each:

```bash
./scripts/test_apis.sh
```

```
== Health ==
  OK   root  (200, count/status=ok)
  OK   sat  (200, count/status=ok)
  OK   nse  (200, count/status=ok)
  OK   sebi  (200, count/status=ok)
  OK   bse  (200, count/status=ok)

== SAT ==
  OK   orders/date-wise  (200, count/status=494)

== NSE ==
  OK   circulars  (200, count/status=555)

== SEBI ==
  OK   circular  (200, count/status=5)
  OK   master-circular  (200, count/status=95)

== BSE ==
  OK   circular  (200, count/status=501)

== Logs ==
  OK   view-logs  (200, count/status=?)

Passed: 11  Failed: 0
```

Exits non-zero if any check fails. Configurable via env vars:

```bash
HOST=127.0.0.1 PORT=8080 SERVICE_TOKEN=mytoken ./scripts/test_apis.sh
```

If it's not executable in your checkout (permissions don't always survive every git transport), run `chmod +x scripts/test_apis.sh` once, or invoke it directly with `bash scripts/test_apis.sh`.

---

## Authentication

Every route under `/api/enforcement_crawler/*` is gated by `require_service_token` (`app/core/auth.py`):

- `SERVICE_TOKEN` **unset/empty** → auth disabled, all requests pass (default in `sample_env.txt`).
- `SERVICE_TOKEN` **set** → every request needs a matching Bearer token, or you get `401`.

```bash
curl -H "Authorization: Bearer $SERVICE_TOKEN" http://localhost:8001/api/enforcement_crawler/health
```

All examples below assume `SERVICE_TOKEN=test_token` (the value in `sample_env.txt`). Adjust or drop the header to match your own `.env`.

---

## Module Overview

| Module | Prefix | Source site | Endpoints |
|---|---|---|---|
| SAT | `/api/enforcement_crawler/sat` | satweb.sat.gov.in | `/health` `/info` `/orders/date-wise` |
| NSE | `/api/enforcement_crawler/nse` | nseindia.com | `/health` `/info` `/circulars` |
| SEBI | `/api/enforcement_crawler/sebi` | sebi.gov.in | `/health` `/info` `/circular` `/master-circular` |
| BSE | `/api/enforcement_crawler/bse` | bseindia.com | `/health` `/info` `/circular` |

Every POST endpoint shares the same response envelope:

```json
{
  "source": "...",
  "url": "...",
  "filters_applied": { "...": "..." },
  "count": 0,
  "records": [ { "...": "..." } ]
}
```

`GET /info` on every module returns the field reference, valid filter values, and upstream-specific notes (session handling, pagination, PDF resolution, etc.) in machine-readable form — check it first if a request behaves unexpectedly.

---

## Health Checks

```bash
AUTH="Authorization: Bearer test_token"

curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/health
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/sat/health
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/nse/health
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/sebi/health
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/bse/health
```

Each returns `{"status": "ok", "module": "<name>"}`.

---

## Logs

Every request (method, path, status code, duration) and every unhandled error is logged to `app.log` at the repo root, in addition to each module's own scrape-level logs.

```bash
curl -H "$AUTH" "http://localhost:8001/api/enforcement_crawler/view-logs"
curl -H "$AUTH" "http://localhost:8001/api/enforcement_crawler/view-logs?lines=100"
curl -H "$AUTH" "http://localhost:8001/api/enforcement_crawler/view-logs?lines=0"   # full file
```

Returns the trailing `lines` (default `500`) of `app.log` as plain text.

---

## SAT — Securities Appellate Tribunal

Scrapes case orders from [satweb.sat.gov.in/orders](https://satweb.sat.gov.in/orders) — "Date Wise" search tab only.

> Requires a stateful cookie session + rotating `security_token`; `via_proxy` defaults to `false` since routing through the proxy breaks cookie persistence. Wide ranges are auto-split into ≤3-month chunks (capped at 40 chunks) and merged.

### Info

```bash
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/sat/info
```

### Fetch orders (date-wise)

`POST /api/enforcement_crawler/sat/orders/date-wise`

| Field | Type | Required | Description |
|---|---|---|---|
| `apl_type` | string | Yes | `sebi` \| `irda` \| `pfrda` |
| `from_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `to_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `via_proxy` | bool | No | Default `false` |

```bash
curl -X POST http://localhost:8001/api/enforcement_crawler/sat/orders/date-wise \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"apl_type": "sebi", "from_date": "01-06-2026", "to_date": "30-06-2026"}'
```

---

## NSE — Circulars

Fetches from the NSE circulars JSON API. Only `pdf`/`zip` attachments are kept.

> `via_proxy` defaults to `false` — NSE requires specific headers (User-Agent, Accept, Referer) that some proxy aggregators strip.

### Info

```bash
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/nse/info
```

### Fetch circulars

`POST /api/enforcement_crawler/nse/circulars`

| Field | Type | Required | Description |
|---|---|---|---|
| `from_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `to_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `via_proxy` | bool | No | Default `false` |

```bash
curl -X POST http://localhost:8001/api/enforcement_crawler/nse/circulars \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"from_date": "01-06-2026", "to_date": "30-06-2026"}'
```

---

## SEBI — Circulars & Master Circulars

Scrapes SEBI's Legal → Circulars / Master Circulars listing pages via their own search AJAX endpoint. Upstream pagination (25 rows/page) is walked automatically and merged (capped at 200 pages / 5,000 records).

> **`download_url` note:** the listing table only links to an HTML landing page. The service follows each landing page and extracts the real PDF link from its embedded viewer (`iframe src=".../web/?file=<download_url>"`), 10 concurrent requests at a time, capped at 300 records per response — falls back to the landing page URL if resolution fails for a given record.

### Info

```bash
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/sebi/info
```

Returns valid `department`/`intermediary` id → name maps for both endpoints.

### Circulars

`POST /api/enforcement_crawler/sebi/circular`

| Field | Type | Required | Description |
|---|---|---|---|
| `from_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `to_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `search` | string | No | Title/keyword/entity search |
| `department` | string | No | Department name (see `/info`). `All Department`/omit = all |
| `intermediary` | string | No | Intermediary name (see `/info`). `All Intermediary`/omit = all |
| `via_proxy` | bool | No | Default `false` |

```bash
curl -X POST http://localhost:8001/api/enforcement_crawler/sebi/circular \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"from_date": "01-06-2026", "to_date": "30-06-2026"}'
```

**Response record shape:**

```json
{
  "date": "Jun 24, 2026",
  "title": "Ease of Doing Business – Relaxation in certification requirement ...",
  "download_url": "https://www.sebi.gov.in/sebi_data/attachdocs/jun-2026/1782299902147.pdf",
  "type": "Circular",
  "department": "All Department"
}
```

### Master Circulars

`POST /api/enforcement_crawler/sebi/master-circular`

| Field | Type | Required | Description |
|---|---|---|---|
| `from_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `to_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `department` | string | Yes | Department name (see `/info`). Use `All Department` for all |
| `via_proxy` | bool | No | Default `false` |

```bash
curl -X POST http://localhost:8001/api/enforcement_crawler/sebi/master-circular \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"from_date": "01-01-2020", "to_date": "30-06-2026", "department": "-1"}'
```

Records omit `department` (not exposed per-row by the upstream table) but otherwise match the Circulars shape, with `"type": "Master Circular"`.

---

## BSE — Notices & Circulars

Fetches from BSE's own listing API (`getDataAdvance`) behind the "Notices & Circulars" page. No upstream pagination — a single request returns the full matching set.

> **`download_url` note:** BSE serves each notice's PDF directly and unauthenticated at `bseindia.com/downloads/UploadDocs/Notices/<Notice_no>/<Notice_no>.pdf`. The service verifies this with a HEAD request per record (10 concurrent, capped at 300 records per response) and falls back to BSE's own gated download endpoint only if verification fails.
>
> **Known upstream limitation — stale search index:** BSE's `getDataAdvance` search API (and even their own "Latest News" homepage widget feed) has been observed lagging significantly behind BSE's actual filings. As of testing, both returned nothing past **2026-03-13**, even though BSE's file storage already has newer PDFs (confirmed directly — `bseindia.com/downloads/UploadDocs/Notices/20260709-54/20260709-54.pdf` serves a live July 2026 PDF). A `count: 0` response for a recent date range is very likely this upstream index lag, **not a bug** — filters/date-parsing were verified correct against a range known to have data. Sanity-check any "no results" by trying a range further in the past, or by checking [bseindia.com/markets/marketinfo/noticescirculars](https://www.bseindia.com/markets/marketinfo/noticescirculars?id=0&txtscripcd=&pagecont=&subject=) directly for the same range.

### Info

```bash
curl -H "$AUTH" http://localhost:8001/api/enforcement_crawler/bse/info
```

Returns the valid `segment` / `category` / `department` value lists.

### Circulars

`POST /api/enforcement_crawler/bse/circular`

| Field | Type | Required | Description |
|---|---|---|---|
| `start_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `end_date` | string | Yes | Inclusive. `DD-MM-YYYY` or `YYYY-MM-DD` |
| `segment` | string | Yes | Must match a value from `/info` (e.g. `Equity`, `Debt`) |
| `category` | string | Yes | Must match a value from `/info` (e.g. `Trading`, `Corporate Actions`) |
| `department` | string | Yes | Must match a value from `/info` (e.g. `Listing Operations`) |
| `subject` | string | No | Free-text subject search |
| `via_proxy` | bool | No | Default `false` |

```bash
curl -X POST http://localhost:8001/api/enforcement_crawler/bse/circular \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{
    "start_date": "01-03-2026",
    "end_date": "31-03-2026",
    "segment": "Debt",
    "category": "Corporate Actions",
    "department": "Listing Operations"
  }'
```

**Response record shape:**

```json
{
  "notice_number": "20260313-59",
  "subject": "Suspension of Trading in Debentures of SG FINSERVE LIMITED",
  "segment": "Debt",
  "category": "Corporate Actions",
  "department": "Listing Operations",
  "download_url": "https://www.bseindia.com/downloads/UploadDocs/Notices/20260313-59/20260313-59.pdf",
  "date": "2026-03-13",
  "type": "BSE Circular"
}
```

---

## Quick Smoke Test (all modules)

This is what [`scripts/test_apis.sh`](scripts/test_apis.sh) runs under the hood — reproduced here as a plain copy-paste block for when you'd rather not run a script (e.g. pasting into a teammate's terminal). **Requires a server already running** (see [Running Locally](#running-locally)) — unlike the script, this block does not start one for you.

```bash
BASE="http://localhost:8001/api/enforcement_crawler"
AUTH="Authorization: Bearer test_token"

echo "--- health ---"
curl -s -H "$AUTH" "$BASE/health"
curl -s -H "$AUTH" "$BASE/sat/health"
curl -s -H "$AUTH" "$BASE/nse/health"
curl -s -H "$AUTH" "$BASE/sebi/health"
curl -s -H "$AUTH" "$BASE/bse/health"

echo "--- sat ---"
curl -s -X POST "$BASE/sat/orders/date-wise" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"apl_type": "sebi", "from_date": "01-06-2026", "to_date": "30-06-2026"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('count', d.get('count'))"

echo "--- nse ---"
curl -s -X POST "$BASE/nse/circulars" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"from_date": "01-06-2026", "to_date": "07-06-2026"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('count', d.get('count'))"

echo "--- sebi circular ---"
curl -s -X POST "$BASE/sebi/circular" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"from_date": "01-06-2026", "to_date": "30-06-2026"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('count', d.get('count'))"

echo "--- sebi master-circular ---"
curl -s -X POST "$BASE/sebi/master-circular" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"from_date": "01-01-2020", "to_date": "30-06-2026", "department": "-1"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('count', d.get('count'))"

echo "--- bse circular ---"
curl -s -X POST "$BASE/bse/circular" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"start_date": "01-03-2026", "end_date": "31-03-2026", "segment": "", "category": "", "department": ""}' | python3 -c "import json,sys; d=json.load(sys.stdin); print('count', d.get('count'))"

echo "--- view-logs ---"
curl -s -H "$AUTH" "$BASE/view-logs?lines=20"
```
