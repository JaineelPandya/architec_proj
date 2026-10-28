
import os
import hashlib
from pathlib import Path

from app.indexing.language_detector import EXTENSION_LANGUAGES
from app.indexing.symbol_index import build_index
from app.indexing.sqlite_store import SQLiteStore
from app.indexing.relationship_resolver import RelationshipResolver


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    "__MACOSX",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    "dist",
    "build",
    "target",
    "vendor",
    ".idea",
    ".vscode",
}

MAX_SOURCE_BYTES = 5 * 1024 * 1024  # 5 MB per file


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_files(root: Path, db_path: Path) -> list[Path]:
    """Discover supported source files while skipping ignored directories."""
    files = []

    for current_dir, dirs, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_dir)

        # Prune ignored folders before walking their contents.
        dirs[:] = [
            name
            for name in dirs
            if name not in IGNORED_DIRS
            and not (current / name).is_symlink()
        ]

        for filename in filenames:
            # Ignore macOS metadata files.
            if filename.startswith("._") or filename == ".DS_Store":
                continue

            path = current / filename

            if path.is_symlink() or not path.is_file():
                continue

            relative = path.relative_to(root)

            # Do not index the generated SQLite index directory.
            if relative.parts[:2] == ("data", "index"):
                continue

            # Only scan supported file extensions.
            if path.suffix.lower() not in EXTENSION_LANGUAGES:
                continue

            # Do not index the database itself.
            if path.resolve() == db_path.resolve():
                continue

            files.append(path)

    return sorted(files)


def index_repository(
    project_root: str | Path,
    db_path: str | Path | None = None,
) -> dict:
    """Index a repository and persist its symbols, imports and calls."""
    root = Path(project_root).resolve()

    if not root.is_dir():
        raise NotADirectoryError(
            f"Project directory not found: {root}"
        )

    if db_path is None:
        db = root / "data" / "index" / "project.sqlite3"
    else:
        db = Path(db_path).resolve()

    store = SQLiteStore(db)

    errors = []
    indexed = 0
    skipped = 0
    current_paths = set()
    changed_paths: set[str] = set()

    try:
        source_files = discover_files(root, db)

        for path in source_files:
            relative_path = path.relative_to(root).as_posix()
            current_paths.add(relative_path)

            try:
                raw = path.read_bytes()
                digest = sha256_bytes(raw)
                language = EXTENSION_LANGUAGES[path.suffix.lower()]

                # Avoid processing unusually large files.
                if len(raw) > MAX_SOURCE_BYTES:
                    errors.append({
                        "file": relative_path,
                        "error": "File exceeds the 5 MB indexing limit",
                    })

                    # Remove stale records for an oversized file.
                    store.replace_file(
                        relative_path,
                        language,
                        digest,
                        len(raw),
                        {
                            "symbols": [],
                            "imports": [],
                            "calls": [],
                        },
                    )
                    changed_paths.add(relative_path)
                    continue

                # Skip unchanged files.
                if store.get_file_hash(relative_path) == digest:
                    skipped += 1
                    continue

                result = build_index(path)

                # Update paths only in structured records.
                # Error messages may be strings, not dictionaries.
                for key in ("symbols", "imports", "calls"):
                    for item in result.get(key, []):
                        item["file"] = relative_path

                store.replace_file(
                    path=relative_path,
                    language=language,
                    sha256=digest,
                    size_bytes=len(raw),
                    result=result,
                )

                indexed += 1
                changed_paths.add(relative_path)

                # Preserve indexing errors in the report.
                for error in result.get("errors", []):
                    errors.append({
                        "file": relative_path,
                        "error": (
                            error
                            if isinstance(error, str)
                            else str(error)
                        ),
                    })

            except Exception as exc:
                errors.append({
                    "file": relative_path,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        # Remove records for files no longer present.
        removed = store.remove_missing_files(current_paths)

        # Wire relationship resolution incrementally after source-file indexes are updated.
        try:
            resolver = RelationshipResolver(store, project_root=root)
            existing_rel_count = store.counts().get("relationships", 0)

            # Idempotent: if nothing changed or removed and relationships already exist, skip.
            if indexed == 0 and removed == 0 and existing_rel_count > 0:
                pass
            elif existing_rel_count == 0 or removed > 0:
                # Initial resolution or files removed: recompute completely for consistency.
                resolver.resolve_all()
            else:
                # Incremental resolution: recompute only changed and dependent files.
                affected_files = resolver.find_affected_files(changed_paths)
                resolver.resolve_files(affected_files)
        except Exception as exc:
            errors.append({
                "file": "relationship_resolution",
                "error": f"{type(exc).__name__}: {exc}",
            })

        return {
            "project_root": str(root),
            "database": str(db),
            "discovered_files": len(source_files),
            "indexed_files": indexed,
            "unchanged_files_skipped": skipped,
            "removed_files": removed,
            "counts": store.counts(),
            "errors": errors,
        }

    finally:
        store.close()
