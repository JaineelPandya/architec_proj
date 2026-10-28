import json
import logging
import logging.config
import re
import time
from enum import Enum
from pathlib import Path
import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse


class LogType(str, Enum):
    nse = "nse"
    bse = "bse"
    sebi = "sebi"
    sebi_circular = "sebi-circular"
    sebi_master_circular = "sebi-master-circular"
    sat = "sat"


class LogLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

from app.core.auth import require_service_token
from app.core.config import settings

load_dotenv(Path(__file__).resolve().parent / ".env")

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE_PATH = LOGS_DIR / f"app-{datetime.date.today().isoformat()}.log"


_REDACT_PATTERNS = [
    re.compile(r"(api[_-]?key|apikey)=([^&\s\"']+)", re.I),
    re.compile(r"(token)=([^&\s\"']{8,})", re.I),
]


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _REDACT_PATTERNS:
            msg = pat.sub(r"\1=***", msg)
        record.msg = msg
        record.args = ()
        return True


def _configure_logging() -> None:
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    if settings.LOG_JSON:
        class _JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "time": self.formatTime(record),
                    "logger": record.name,
                    "level": record.levelname,
                    "message": record.getMessage(),
                }
                if record.exc_info:
                    payload["exc"] = self.formatException(record.exc_info)
                return json.dumps(payload)

        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(fmt))

    # Ensure logs directory exists
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # If directory creation fails, fall back to parent directory
        LOG_FILE_PATH = Path(__file__).resolve().parent.parent / "app.log"

    # Daily-rotating, date-stamped active filename handler.
    class DailyRotatingFileHandler(logging.Handler):
        def __init__(self, logs_dir: Path, backup_count: int = 30, encoding: Optional[str] = None):
            super().__init__()
            self.logs_dir = Path(logs_dir)
            self.backup_count = int(backup_count)
            self.encoding = encoding
            self._current_date = None
            self._delegate: Optional[logging.FileHandler] = None
            self._ensure_delegate()

        def _current_filename(self) -> Path:
            today = datetime.date.today().isoformat()
            return self.logs_dir / f"app-{today}.log"

        def _ensure_delegate(self):
            desired = self._current_filename()
            if self._delegate is None or Path(self._delegate.baseFilename) != desired:
                # Close old delegate if present
                if self._delegate:
                    try:
                        self._delegate.close()
                    except Exception:
                        pass
                # Open new FileHandler for today's file
                fh = logging.FileHandler(desired, mode="a", encoding=self.encoding)
                # propagate formatter if set
                if hasattr(self, "formatter") and self.formatter:
                    fh.setFormatter(self.formatter)
                self._delegate = fh

                # Cleanup old files beyond backup_count
                try:
                    files = sorted(self.logs_dir.glob("app-*.log"))
                    if len(files) > self.backup_count:
                        # Keep most recent backup_count files
                        for old in files[: len(files) - self.backup_count]:
                            try:
                                old.unlink()
                            except Exception:
                                pass
                except Exception:
                    pass

        def setFormatter(self, fmt: logging.Formatter) -> None:
            super().setFormatter(fmt)
            if self._delegate:
                self._delegate.setFormatter(fmt)

        def emit(self, record: logging.LogRecord) -> None:
            try:
                # Ensure delegate is for current date
                self._ensure_delegate()
                if self._delegate:
                    self._delegate.emit(record)
            except Exception:
                self.handleError(record)

        def close(self) -> None:
            try:
                if self._delegate:
                    try:
                        self._delegate.close()
                    except Exception:
                        pass
            finally:
                super().close()

    file_handler = DailyRotatingFileHandler(LOGS_DIR, backup_count=30, encoding="utf-8")
    # Ensure the formatted output matches the configured format
    file_handler.setFormatter(logging.Formatter(fmt))

    redact = _RedactingFilter()
    handler.addFilter(redact)
    file_handler.addFilter(redact)

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        handlers=[handler, file_handler],
        force=True,
    )


_configure_logging()
logger = logging.getLogger(__name__)


app = FastAPI(
    title="Enforcement Crawler API",
    description="Regulatory circulars, orders and notices crawling API for SEBI, BSE, NSE and SAT",
    version="1.0.0",
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        f"{request.method} {request.url.path} status={response.status_code} duration_ms={duration_ms:.1f}"
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


from app.router.sat import router as sat_router
from app.router.nse import router as nse_router
from app.router.nse_regulations import router as nse_regulations_router
from app.router.sebi import router as sebi_router
from app.router.bse import router as bse_router
from fastapi import APIRouter, Depends

_auth = [Depends(require_service_token)]

main_router = APIRouter(prefix="/api/enforcement_crawler", dependencies=_auth)
main_router.include_router(sat_router, prefix="/sat", tags=["SAT"])
main_router.include_router(nse_router, prefix="/nse", tags=["NSE"])
main_router.include_router(nse_regulations_router, prefix="/nse-regulations", tags=["NSE Regulations"])
main_router.include_router(sebi_router, prefix="/sebi", tags=["SEBI"])
main_router.include_router(bse_router, prefix="/bse", tags=["BSE"])


@main_router.get("/view-logs")
def view_logs(
    dd: Optional[int] = Query(None, ge=1, le=31, description="Day"),
    mm: Optional[int] = Query(None, ge=1, le=12, description="Month"),
    yyyy: Optional[int] = Query(None, ge=1900, le=2100, description="Year"),
    type: Optional[LogType] = None,
    level: Optional[LogLevel] = None,
    status: Optional[int] = None,
    min_duration_ms: Optional[float] = None,
    q: Optional[str] = None,
    exclude_httpx: bool = False,
    lines: int = 500,
    list_dates: bool = False,
):
    """Return logs with optional filtering and list available dates.

    Filtering is applied first; `lines` caps the filtered results (0 => all).
    """
    # If requested, list available dates
    if list_dates:
        if not LOGS_DIR.exists():
            return JSONResponse(status_code=200, content={"dates": []})
        dates = []
        for p in sorted(LOGS_DIR.glob("app-*.log"), reverse=True):
            name = p.name
            if name.startswith("app-") and name.endswith(".log"):
                dates.append(name[len("app-") : -len(".log")])
        return JSONResponse(status_code=200, content={"dates": dates})

    # Construct date from broken dd/mm/yyyy if provided
    if dd is not None or mm is not None or yyyy is not None:
        if dd is None or mm is None or yyyy is None:
            return PlainTextResponse("Provide all of dd, mm, and yyyy together.", status_code=400)
        try:
            date_obj = datetime.date(year=yyyy, month=mm, day=dd)
            date = date_obj.isoformat()
        except ValueError:
            return PlainTextResponse("Invalid dd/mm/yyyy date.", status_code=400)
    else:
        date = datetime.date.today().isoformat()

    # Default to today if no broken date is provided
    try:
        target = LOGS_DIR / f"app-{date}.log"
    except Exception:
        return PlainTextResponse("Invalid date parameter.", status_code=400)

    if not target.exists():
        return PlainTextResponse(f"{target} does not exist yet.", status_code=404)

    content = target.read_text(errors="replace")
    out_lines = []

    for raw in content.splitlines():
        if not raw.strip():
            continue
        # Split into date, time, logger, level, rest
        parts = raw.split(maxsplit=4)
        if len(parts) < 5:
            # If line doesn't match expected format, include it only if no filters
            if any([type, level, status, min_duration_ms, q, exclude_httpx]):
                continue
            out_lines.append(raw)
            continue

        date_tok, time_tok, logger_tok, level_tok, rest = parts

        # Apply exclude_httpx
        if exclude_httpx and (logger_tok == "httpx" or logger_tok.startswith("httpx.")):
            continue

        # Filter by type (nse/bse/sebi/sat)
        if type:
            t = type.lower()
            ok = False
            if logger_tok.startswith(f"app.router.{t}"):
                ok = True
            if logger_tok.startswith(f"app.services.{t}.service"):
                ok = True
            if not ok:
                continue

        # Filter by level
        if level:
            if level_tok.upper() != level.upper():
                continue

        # Filter by status in rest
        if status is not None:
            idx = rest.find("status=")
            if idx == -1:
                continue
            # extract integer after status=
            try:
                after = rest[idx + len("status="):]
                status_str = "".join(ch for ch in after if (ch.isdigit()))
                if not status_str:
                    continue
                if int(status_str) != int(status):
                    continue
            except Exception:
                continue

        # Filter by min_duration_ms in rest
        if min_duration_ms is not None:
            idx = rest.find("duration_ms=")
            if idx == -1:
                continue
            try:
                after = rest[idx + len("duration_ms="):]
                # read up to first non-number/period
                dur_chars = []
                for ch in after:
                    if ch.isdigit() or ch == ".":
                        dur_chars.append(ch)
                    else:
                        break
                if not dur_chars:
                    continue
                if float("".join(dur_chars)) < float(min_duration_ms):
                    continue
            except Exception:
                continue

        # Free-text query
        if q and q.lower() not in rest.lower():
            continue

        out_lines.append(raw)

    # Apply lines cap
    if lines > 0:
        out_lines = out_lines[-lines:]

    return PlainTextResponse("\n".join(out_lines))


app.include_router(main_router)


@app.get("/api/enforcement_crawler")
def root():
    return {"message": "Welcome to Enforcement Crawler API"}


@app.get("/api/enforcement_crawler/health")
def health_check():
    return {"status": "ok"}
