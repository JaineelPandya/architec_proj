from pathlib import Path
import ast
import json


IGNORED_DIRS = {
    ".git", ".venv", "venv", "__pycache__",
    "node_modules", ".idea", ".pytest_cache",
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".cpp", ".c",
}


def analyse_project(project_dir: Path) -> dict:
    files = []
    python_modules = []
    dependencies = set()

    for path in project_dir.rglob("*"):
        if not path.is_file():
            continue

        relative = path.relative_to(project_dir)

        if any(part in IGNORED_DIRS for part in relative.parts):
            continue

        if path.suffix not in SOURCE_EXTENSIONS and path.name not in {
            "requirements.txt", "pyproject.toml",
            "package.json", "Dockerfile",
        }:
            continue

        files.append(str(relative))

        if path.suffix == ".py":
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)

                imports = []
                symbols = []

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.extend(
                            alias.name for alias in node.names
                        )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append({
                            "type": "function",
                            "name": node.name,
                            "line": node.lineno,
                        })
                    elif isinstance(node, ast.ClassDef):
                        symbols.append({
                            "type": "class",
                            "name": node.name,
                            "line": node.lineno,
                        })

                python_modules.append({
                    "file": str(relative),
                    "imports": sorted(set(imports)),
                    "symbols": symbols,
                })

                dependencies.update(imports)

            except (SyntaxError, UnicodeDecodeError, OSError):
                python_modules.append({
                    "file": str(relative),
                    "error": "Could not parse Python source",
                })

    return {
        "project_root": str(project_dir),
        "file_count": len(files),
        "files": sorted(files),
        "python_module_count": len(python_modules),
        "python_modules": python_modules,
        "import_names": sorted(dependencies),
    }


def save_report(report: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "project_analysis.json"
    report_path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    return report_path