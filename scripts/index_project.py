
import argparse
import json
from pathlib import Path

from app.indexing.repository_indexer import index_repository


def main():
    parser = argparse.ArgumentParser(
        description="Index a source-code repository into SQLite."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project directory to index",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Optional SQLite database path",
    )
    args = parser.parse_args()

    report = index_repository(
        project_root=Path(args.project),
        db_path=args.db,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
