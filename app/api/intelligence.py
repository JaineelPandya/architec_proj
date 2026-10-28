from collections.abc import Generator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.indexing.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/v1", tags=["Code Intelligence"])

DEFAULT_DB_PATH = Path("data/index/project.sqlite3")
VALID_RELATIONSHIP_TYPES = {"imports", "calls", "defines_method", "inherits"}
VALID_RELATIONSHIP_STATUSES = {"resolved", "ambiguous", "unresolved"}


def get_store(
    request: Request,
    db_path: str | None = Query(None, description="Optional custom database path"),
) -> Generator[SQLiteStore, None, None]:
    """Provide a SQLiteStore connection for the request."""
    if db_path:
        target = Path(db_path)
    elif hasattr(request.app.state, "db_path"):
        target = Path(request.app.state.db_path)
    else:
        target = DEFAULT_DB_PATH

    store = SQLiteStore(target)
    try:
        yield store
    finally:
        store.close()


@router.get("/index/stats")
def get_index_stats(
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Return repository index statistics."""
    return store.counts()


@router.get("/symbols/search")
def search_symbols(
    q: str = Query(..., description="Symbol name to search"),
    type: str | None = Query(None, description="Filter by symbol type (e.g. function, class, method)"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of results"),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Search indexed symbol names with optional type filtering."""
    query_term = q.strip()
    if not query_term:
        raise HTTPException(
            status_code=400,
            detail="Query parameter 'q' must not be empty.",
        )

    symbols = store.search_symbols(
        name=query_term,
        symbol_type=type.strip() if type else None,
        limit=limit,
    )

    return {
        "query": query_term,
        "count": len(symbols),
        "symbols": symbols,
    }


@router.get("/files/{path:path}/dependencies")
def get_file_dependencies(
    path: str,
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Get all outgoing dependencies (imports, calls, inheritance) for a file."""
    if not store.file_exists(path):
        raise HTTPException(
            status_code=404,
            detail=f"File not found in index: {path}",
        )

    records = store.get_file_dependencies(path)

    # Distinguish resolved, ambiguous, and unresolved dependencies
    resolved_files = sorted({
        r["target_file"]
        for r in records
        if r["status"] == "resolved" and r["target_file"] and r["target_file"] != path
    })
    ambiguous_candidates = sorted({
        r["target_file"]
        for r in records
        if r["status"] == "ambiguous" and r["target_file"] and r["target_file"] != path
    })
    unresolved_count = sum(1 for r in records if r["status"] == "unresolved")

    return {
        "file": path,
        "resolved_files": resolved_files,
        "ambiguous_candidates": ambiguous_candidates,
        "unresolved_count": unresolved_count,
        "total_dependencies": len(records),
        "dependencies": records,
    }


@router.get("/files/{path:path}/dependents")
def get_file_dependents(
    path: str,
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Get all files that depend on or reference the specified file."""
    if not store.file_exists(path):
        raise HTTPException(
            status_code=404,
            detail=f"File not found in index: {path}",
        )

    records = store.get_file_dependents(path)

    dependent_files = sorted({
        r["source_file"]
        for r in records
        if r["status"] == "resolved" and r["source_file"] != path
    })
    ambiguous_dependent_files = sorted({
        r["source_file"]
        for r in records
        if r["status"] == "ambiguous" and r["source_file"] != path
    })

    return {
        "file": path,
        "dependent_files": dependent_files,
        "ambiguous_dependent_files": ambiguous_dependent_files,
        "total_dependents": len(records),
        "dependents": records,
    }


@router.get("/symbols/callers")
def get_symbol_callers(
    name: str = Query(..., description="Target symbol name"),
    file_path: str | None = Query(None, description="Optional target file path"),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Find call sites referencing the specified symbol."""
    symbol_name = name.strip()
    if not symbol_name:
        raise HTTPException(
            status_code=400,
            detail="Query parameter 'name' must not be empty.",
        )

    callers = store.get_symbol_callers(
        name=symbol_name,
        file_path=file_path.strip() if file_path else None,
    )

    return {
        "symbol": symbol_name,
        "file_path": file_path,
        "count": len(callers),
        "callers": callers,
    }


@router.get("/symbols/callees")
def get_symbol_callees(
    name: str = Query(..., description="Caller symbol scope or function name"),
    file_path: str | None = Query(None, description="Optional caller file path"),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Find symbols called from within the specified symbol scope."""
    symbol_name = name.strip()
    if not symbol_name:
        raise HTTPException(
            status_code=400,
            detail="Query parameter 'name' must not be empty.",
        )

    callees = store.get_symbol_callees(
        name=symbol_name,
        file_path=file_path.strip() if file_path else None,
    )

    return {
        "symbol": symbol_name,
        "file_path": file_path,
        "count": len(callees),
        "callees": callees,
    }


@router.get("/relationships")
def get_relationships(
    source_file: str | None = Query(None, description="Filter by source file"),
    target_file: str | None = Query(None, description="Filter by target file"),
    type: str | None = Query(None, description="Filter by relationship type (imports, calls, defines_method, inherits)"),
    status: str | None = Query(None, description="Filter by resolution status (resolved, ambiguous, unresolved)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of relationships to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    store: SQLiteStore = Depends(get_store),
) -> dict:
    """Filter relationships by source file, target file, type, and status with pagination."""
    if type is not None and type not in VALID_RELATIONSHIP_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{type}'. Valid types are: {', '.join(sorted(VALID_RELATIONSHIP_TYPES))}.",
        )

    if status is not None and status not in VALID_RELATIONSHIP_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Valid statuses are: {', '.join(sorted(VALID_RELATIONSHIP_STATUSES))}.",
        )

    total, records = store.get_filtered_relationships(
        source_file=source_file.strip() if source_file else None,
        target_file=target_file.strip() if target_file else None,
        type=type,
        status=status,
        limit=limit,
        offset=offset,
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "relationships": records,
    }
