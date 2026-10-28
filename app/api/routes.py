from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, BadZipFile
import shutil

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.project_generator import (
    analyse_project,
    save_report,
)

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])

WORKSPACE = Path("data/workspace")
REPORTS = Path("generated")


@router.post("/analyse")
async def analyse_uploaded_project(
    file: UploadFile = File(...),
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="Upload a ZIP file.",
        )

    project_id = uuid4().hex
    project_dir = WORKSPACE / project_id
    archive_path = WORKSPACE / f"{project_id}.zip"

    WORKSPACE.mkdir(parents=True, exist_ok=True)

    try:
        # Stream upload to disk with a size limit.
        size = 0
        max_size = 100 * 1024 * 1024

        with archive_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > max_size:
                    raise HTTPException(
                        status_code=413,
                        detail="ZIP exceeds the 100 MB limit.",
                    )

                destination.write(chunk)

        # Inspect the archive before extracting it.
        with ZipFile(archive_path) as archive:
            entries = archive.infolist()

            if len(entries) > 10000:
                raise HTTPException(
                    status_code=400,
                    detail="ZIP contains too many files.",
                )

            total_uncompressed = sum(
                entry.file_size for entry in entries
            )

            if total_uncompressed > 500 * 1024 * 1024:
                raise HTTPException(
                    status_code=400,
                    detail="Uncompressed project exceeds 500 MB.",
                )

            root = project_dir.resolve()

            for entry in entries:
                target = (project_dir / entry.filename).resolve()

                if not target.is_relative_to(root):
                    raise HTTPException(
                        status_code=400,
                        detail="Unsafe path found in ZIP.",
                    )

            project_dir.mkdir(parents=True, exist_ok=True)
            archive.extractall(project_dir)

        # Detect the actual project root.
        children = list(project_dir.iterdir())

        if len(children) == 1 and children[0].is_dir():
            candidate = children[0]
            if any(candidate.rglob("*.py")):
                project_root = candidate
            else:
                project_root = project_dir
        else:
            project_root = project_dir

        report = analyse_project(project_root)
        report["project_id"] = project_id

        report_path = save_report(
            report,
            REPORTS / project_id,
        )

        return {
            "project_id": project_id,
            "status": "analysed",
            "analysis": report,
            "report_path": str(report_path),
        }

    except BadZipFile:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is not a valid ZIP archive.",
        )
    finally:
        archive_path.unlink(missing_ok=True)
        await file.close()