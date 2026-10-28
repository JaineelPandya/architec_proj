import ast
from pathlib import Path
from typing import Any

from app.indexing.sqlite_store import SQLiteStore


PYTHON_BUILTINS = set(dir(__builtins__))


class ResolutionContext:
    """Read-only view of indexed symbols, imports, and files across the repository."""

    def __init__(self, store: SQLiteStore, project_root: Path | None = None):
        self.store = store
        self.project_root = project_root
        self.conn = store.connection

        # Load all indexed files and their languages.
        file_rows = self.conn.execute("SELECT path, language FROM files").fetchall()
        self.files: dict[str, str] = {row["path"]: row["language"] for row in file_rows}
        self.repo_files: set[str] = set(self.files.keys())

        # Load all symbols across the repository.
        sym_rows = self.conn.execute(
            "SELECT id, file_path, name, type, language, line, end_line, scope FROM symbols"
        ).fetchall()

        self.symbols_by_file: dict[str, list[dict]] = {}
        self.symbols_by_name: dict[str, list[dict]] = {}
        self.symbols_by_file_and_name: dict[tuple[str, str], list[dict]] = {}
        self.symbols_by_file_scope_name: dict[tuple[str, str, str], dict] = {}
        self.classes_by_file: dict[str, list[dict]] = {}

        for row in sym_rows:
            sym = dict(row)
            fpath = sym["file_path"]
            sname = sym["name"]
            scope = sym["scope"] or ""

            self.symbols_by_file.setdefault(fpath, []).append(sym)
            self.symbols_by_name.setdefault(sname, []).append(sym)
            self.symbols_by_file_and_name.setdefault((fpath, sname), []).append(sym)
            self.symbols_by_file_scope_name[(fpath, scope, sname)] = sym

            if sym["type"] == "class":
                self.classes_by_file.setdefault(fpath, []).append(sym)


class LanguageRelationshipAdapter:
    """Base interface for language-specific relationship resolution."""

    def resolve_file(
        self,
        file_path: str,
        context: ResolutionContext,
    ) -> list[dict]:
        raise NotImplementedError


class FallbackLanguageResolver(LanguageRelationshipAdapter):
    """Safe fallback for languages without dedicated semantic resolution."""

    def resolve_file(
        self,
        file_path: str,
        context: ResolutionContext,
    ) -> list[dict]:
        relationships: list[dict] = []
        conn = context.conn

        # Method definitions can be reliably extracted from symbol scopes.
        symbols = context.symbols_by_file.get(file_path, [])
        for sym in symbols:
            if sym["type"] == "method" and sym.get("scope"):
                relationships.append({
                    "source_file": file_path,
                    "source_symbol": sym["scope"],
                    "target_file": file_path,
                    "target_symbol": sym["name"],
                    "target_symbol_id": sym["id"],
                    "type": "defines_method",
                    "line": sym["line"],
                    "status": "resolved",
                })

        # Do not fabricate cross-language connections for external or unverified calls.
        calls = conn.execute(
            "SELECT name, line FROM calls WHERE file_path = ?", (file_path,)
        ).fetchall()
        for call in calls:
            relationships.append({
                "source_file": file_path,
                "source_symbol": None,
                "target_file": None,
                "target_symbol": call["name"],
                "target_symbol_id": None,
                "type": "calls",
                "line": call["line"],
                "status": "unresolved",
            })

        imports = conn.execute(
            "SELECT text, line FROM imports WHERE file_path = ?", (file_path,)
        ).fetchall()
        for imp in imports:
            relationships.append({
                "source_file": file_path,
                "source_symbol": None,
                "target_file": None,
                "target_symbol": imp["text"],
                "target_symbol_id": None,
                "type": "imports",
                "line": imp["line"],
                "status": "unresolved",
            })

        return relationships


class PythonRelationshipResolver(LanguageRelationshipAdapter):
    """Python semantic relationship resolver supporting imports, calls, methods, and inheritance."""

    def resolve_file(
        self,
        file_path: str,
        context: ResolutionContext,
    ) -> list[dict]:
        relationships: list[dict] = []

        # 1. Resolve method definitions from symbol table.
        symbols = context.symbols_by_file.get(file_path, [])
        for sym in symbols:
            if sym["type"] == "method" and sym.get("scope"):
                relationships.append({
                    "source_file": file_path,
                    "source_symbol": sym["scope"],
                    "target_file": file_path,
                    "target_symbol": sym["name"],
                    "target_symbol_id": sym["id"],
                    "type": "defines_method",
                    "line": sym["line"],
                    "status": "resolved",
                })

        # 2. Resolve imports and build file-level import map.
        import_map = self._resolve_imports(file_path, context, relationships)

        # 3. Resolve class inheritance.
        class_bases = self._resolve_inheritance(file_path, context, import_map, relationships)

        # 4. Resolve function and method calls.
        self._resolve_calls(file_path, context, import_map, class_bases, relationships)

        return relationships

    def _match_repo_module(
        self,
        module_parts: list[str],
        repo_files: set[str],
    ) -> str | None:
        """Find a matching file in the repository for a sequence of module name parts."""
        if not module_parts:
            return None

        joined = "/".join(module_parts)
        candidate_file = f"{joined}.py"
        if candidate_file in repo_files:
            return candidate_file

        candidate_pkg = f"{joined}/__init__.py"
        if candidate_pkg in repo_files:
            return candidate_pkg

        # Check sub-directory / src prefixes if present in repo.
        for prefix in ("src",):
            prefixed_file = f"{prefix}/{candidate_file}"
            if prefixed_file in repo_files:
                return prefixed_file
            prefixed_pkg = f"{prefix}/{candidate_pkg}"
            if prefixed_pkg in repo_files:
                return prefixed_pkg

        return None

    def _resolve_imports(
        self,
        file_path: str,
        context: ResolutionContext,
        relationships: list[dict],
    ) -> dict[str, dict]:
        """Resolve import records to repository files or mark external imports unresolved.
        Returns a local symbol mapping: local_name -> target info.
        """
        import_map: dict[str, dict] = {}
        rows = context.conn.execute(
            "SELECT text, line FROM imports WHERE file_path = ?", (file_path,)
        ).fetchall()

        source_dir_parts = list(Path(file_path).parent.parts)
        if source_dir_parts == ["."] or source_dir_parts == [""]:
            source_dir_parts = []

        for row in rows:
            text = row["text"].strip()
            line = row["line"]

            try:
                parsed = ast.parse(text)
            except SyntaxError:
                relationships.append({
                    "source_file": file_path,
                    "source_symbol": None,
                    "target_file": None,
                    "target_symbol": text,
                    "target_symbol_id": None,
                    "type": "imports",
                    "line": line,
                    "status": "unresolved",
                })
                continue

            for stmt in parsed.body:
                if isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        mod_name = alias.name
                        local_name = alias.asname or (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
                        mod_parts = mod_name.split(".")
                        matched_file = self._match_repo_module(mod_parts, context.repo_files)

                        if matched_file:
                            relationships.append({
                                "source_file": file_path,
                                "source_symbol": None,
                                "target_file": matched_file,
                                "target_symbol": mod_name,
                                "target_symbol_id": None,
                                "type": "imports",
                                "line": line,
                                "status": "resolved",
                            })
                            import_map[local_name] = {
                                "target_file": matched_file,
                                "target_symbol": mod_name,
                                "target_symbol_id": None,
                                "is_module": True,
                                "module_name": mod_name,
                                "status": "resolved",
                            }
                            if alias.asname:
                                import_map[alias.asname] = import_map[local_name]
                        else:
                            relationships.append({
                                "source_file": file_path,
                                "source_symbol": None,
                                "target_file": None,
                                "target_symbol": mod_name,
                                "target_symbol_id": None,
                                "type": "imports",
                                "line": line,
                                "status": "unresolved",
                            })
                            import_map[local_name] = {
                                "target_file": None,
                                "target_symbol": mod_name,
                                "target_symbol_id": None,
                                "is_module": True,
                                "module_name": mod_name,
                                "status": "unresolved",
                            }

                elif isinstance(stmt, ast.ImportFrom):
                    level = stmt.level or 0
                    module = stmt.module or ""

                    # Calculate base directory parts for relative imports.
                    if level > 0:
                        up = level - 1
                        if up <= len(source_dir_parts):
                            base_dir_parts = (
                                source_dir_parts[: len(source_dir_parts) - up]
                                if up > 0
                                else source_dir_parts[:]
                            )
                        else:
                            base_dir_parts = []
                        mod_parts = base_dir_parts + (module.split(".") if module else [])
                    else:
                        mod_parts = module.split(".") if module else []

                    module_file = self._match_repo_module(mod_parts, context.repo_files)

                    for alias in stmt.names:
                        imported_name = alias.name
                        local_name = alias.asname or imported_name

                        if imported_name == "*":
                            # Star import from module_file if resolved.
                            if module_file:
                                relationships.append({
                                    "source_file": file_path,
                                    "source_symbol": None,
                                    "target_file": module_file,
                                    "target_symbol": "*",
                                    "target_symbol_id": None,
                                    "type": "imports",
                                    "line": line,
                                    "status": "resolved",
                                })
                            else:
                                relationships.append({
                                    "source_file": file_path,
                                    "source_symbol": None,
                                    "target_file": None,
                                    "target_symbol": f"{module}.*",
                                    "target_symbol_id": None,
                                    "type": "imports",
                                    "line": line,
                                    "status": "unresolved",
                                })
                            continue

                        # Case 1: The imported name is a submodule file inside the package.
                        submodule_parts = mod_parts + [imported_name]
                        submodule_file = self._match_repo_module(submodule_parts, context.repo_files)

                        if submodule_file:
                            relationships.append({
                                "source_file": file_path,
                                "source_symbol": None,
                                "target_file": submodule_file,
                                "target_symbol": imported_name,
                                "target_symbol_id": None,
                                "type": "imports",
                                "line": line,
                                "status": "resolved",
                            })
                            import_map[local_name] = {
                                "target_file": submodule_file,
                                "target_symbol": imported_name,
                                "target_symbol_id": None,
                                "is_module": True,
                                "status": "resolved",
                            }
                        elif module_file:
                            # Case 2: The imported name is a symbol defined inside module_file.
                            target_sym_id = None
                            matching_symbols = context.symbols_by_file_and_name.get(
                                (module_file, imported_name), []
                            )
                            if matching_symbols:
                                target_sym_id = matching_symbols[0]["id"]

                            relationships.append({
                                "source_file": file_path,
                                "source_symbol": None,
                                "target_file": module_file,
                                "target_symbol": imported_name,
                                "target_symbol_id": target_sym_id,
                                "type": "imports",
                                "line": line,
                                "status": "resolved",
                            })
                            import_map[local_name] = {
                                "target_file": module_file,
                                "target_symbol": imported_name,
                                "target_symbol_id": target_sym_id,
                                "is_module": False,
                                "status": "resolved",
                            }
                        else:
                            # External or unresolvable import.
                            full_name = f"{module}.{imported_name}" if module else imported_name
                            relationships.append({
                                "source_file": file_path,
                                "source_symbol": None,
                                "target_file": None,
                                "target_symbol": full_name,
                                "target_symbol_id": None,
                                "type": "imports",
                                "line": line,
                                "status": "unresolved",
                            })
                            import_map[local_name] = {
                                "target_file": None,
                                "target_symbol": full_name,
                                "target_symbol_id": None,
                                "is_module": False,
                                "status": "unresolved",
                            }

        return import_map

    def _resolve_inheritance(
        self,
        file_path: str,
        context: ResolutionContext,
        import_map: dict[str, dict],
        relationships: list[dict],
    ) -> dict[str, list[dict]]:
        """Resolve class inheritance (node.bases) to repository classes or external targets."""
        class_bases: dict[str, list[dict]] = {}

        source_code = None
        if context.project_root:
            full_path = context.project_root / file_path
            try:
                source_code = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                source_code = None

        if not source_code:
            return class_bases

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return class_bases

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            class_name = node.name
            class_bases[class_name] = []

            for base in node.bases:
                base_name = ast.unparse(base).strip()
                target_file = None
                target_symbol = base_name
                target_symbol_id = None
                status = "unresolved"

                # Check 1: Base class defined in the same file.
                local_classes = [
                    s
                    for s in context.symbols_by_file_and_name.get((file_path, base_name), [])
                    if s["type"] == "class"
                ]
                if local_classes:
                    target_file = file_path
                    target_symbol = base_name
                    target_symbol_id = local_classes[0]["id"]
                    status = "resolved"
                # Check 2: Base class imported directly.
                elif base_name in import_map:
                    mapping = import_map[base_name]
                    if mapping["status"] == "resolved" and mapping["target_file"]:
                        target_file = mapping["target_file"]
                        target_symbol = mapping["target_symbol"]
                        target_symbol_id = mapping.get("target_symbol_id")
                        status = "resolved"
                # Check 3: Dotted base class (e.g. module.BaseClass).
                elif "." in base_name:
                    prefix, attr = base_name.split(".", 1)
                    if prefix in import_map:
                        mapping = import_map[prefix]
                        if mapping["status"] == "resolved" and mapping["target_file"]:
                            mod_file = mapping["target_file"]
                            syms = [
                                s
                                for s in context.symbols_by_file_and_name.get((mod_file, attr), [])
                                if s["type"] == "class"
                            ]
                            target_file = mod_file
                            target_symbol = attr
                            if syms:
                                target_symbol_id = syms[0]["id"]
                                status = "resolved"
                            else:
                                status = "resolved"

                base_info = {
                    "base_name": base_name,
                    "target_file": target_file,
                    "target_symbol": target_symbol,
                    "target_symbol_id": target_symbol_id,
                    "status": status,
                }
                class_bases[class_name].append(base_info)

                relationships.append({
                    "source_file": file_path,
                    "source_symbol": class_name,
                    "target_file": target_file,
                    "target_symbol": target_symbol,
                    "target_symbol_id": target_symbol_id,
                    "type": "inherits",
                    "line": node.lineno,
                    "status": status,
                })

        return class_bases

    def _find_enclosing_symbol(
        self,
        file_path: str,
        line: int,
        context: ResolutionContext,
    ) -> tuple[str | None, str | None]:
        """Find (source_symbol, enclosing_class) for a given call line."""
        symbols = context.symbols_by_file.get(file_path, [])
        candidates = [
            s
            for s in symbols
            if s["line"] <= line <= s.get("end_line", s["line"])
        ]
        if not candidates:
            return None, None

        # Pick candidate with tightest span.
        tightest = min(candidates, key=lambda s: s.get("end_line", s["line"]) - s["line"])

        if tightest["type"] == "method":
            source_symbol = f"{tightest['scope']}.{tightest['name']}" if tightest.get("scope") else tightest["name"]
            enclosing_class = tightest.get("scope") or None
            return source_symbol, enclosing_class
        elif tightest["type"] == "class":
            return tightest["name"], tightest["name"]
        else:
            return tightest["name"], None

    def _resolve_calls(
        self,
        file_path: str,
        context: ResolutionContext,
        import_map: dict[str, dict],
        class_bases: dict[str, list[dict]],
        relationships: list[dict],
    ) -> None:
        """Resolve call expressions using scopes, imports, and symbol definitions."""
        rows = context.conn.execute(
            "SELECT name, line FROM calls WHERE file_path = ?", (file_path,)
        ).fetchall()

        for row in rows:
            name = row["name"].strip()
            line = row["line"]

            source_symbol, enclosing_class = self._find_enclosing_symbol(file_path, line, context)

            # Case A: self.method() or cls.method()
            if name.startswith("self.") or name.startswith("cls."):
                method_name = name.split(".", 1)[1]
                resolved_method = None

                if enclosing_class:
                    # Check local class method definition.
                    local_method = context.symbols_by_file_scope_name.get(
                        (file_path, enclosing_class, method_name)
                    )
                    if local_method:
                        resolved_method = (
                            file_path,
                            f"{enclosing_class}.{method_name}",
                            local_method["id"],
                        )
                    else:
                        # Check inherited methods from resolved base classes.
                        bases = class_bases.get(enclosing_class, [])
                        for base_info in bases:
                            if base_info["status"] == "resolved" and base_info["target_file"]:
                                b_file = base_info["target_file"]
                                b_class = base_info["target_symbol"]
                                inherited = context.symbols_by_file_scope_name.get(
                                    (b_file, b_class, method_name)
                                )
                                if inherited:
                                    resolved_method = (
                                        b_file,
                                        f"{b_class}.{method_name}",
                                        inherited["id"],
                                    )
                                    break

                if resolved_method:
                    relationships.append({
                        "source_file": file_path,
                        "source_symbol": source_symbol,
                        "target_file": resolved_method[0],
                        "target_symbol": resolved_method[1],
                        "target_symbol_id": resolved_method[2],
                        "type": "calls",
                        "line": line,
                        "status": "resolved",
                    })
                else:
                    relationships.append({
                        "source_file": file_path,
                        "source_symbol": source_symbol,
                        "target_file": None,
                        "target_symbol": name,
                        "target_symbol_id": None,
                        "type": "calls",
                        "line": line,
                        "status": "unresolved",
                    })
                continue

            # Case B: Dotted call (e.g. module.func() or object.method())
            if "." in name:
                parts = name.split(".")
                receiver = parts[0]
                target_attr = parts[-1]

                # Check if receiver is an imported module/alias.
                if receiver in import_map:
                    mapping = import_map[receiver]
                    if mapping["status"] == "resolved" and mapping["target_file"]:
                        mod_file = mapping["target_file"]
                        matching_syms = context.symbols_by_file_and_name.get(
                            (mod_file, target_attr), []
                        )
                        target_id = matching_syms[0]["id"] if matching_syms else None
                        relationships.append({
                            "source_file": file_path,
                            "source_symbol": source_symbol,
                            "target_file": mod_file,
                            "target_symbol": target_attr,
                            "target_symbol_id": target_id,
                            "type": "calls",
                            "line": line,
                            "status": "resolved",
                        })
                    else:
                        relationships.append({
                            "source_file": file_path,
                            "source_symbol": source_symbol,
                            "target_file": None,
                            "target_symbol": name,
                            "target_symbol_id": None,
                            "type": "calls",
                            "line": line,
                            "status": "unresolved",
                        })
                    continue

                # Receiver is an object or expression (e.g. crawler.fetch()).
                # Search candidate methods matching target_attr across the repository.
                candidates = [
                    s
                    for s in context.symbols_by_name.get(target_attr, [])
                    if s["type"] in ("method", "function")
                ]

                if len(candidates) > 1:
                    # Multiple candidates: preserve all candidates with status='ambiguous'.
                    for cand in candidates:
                        cand_scope = cand.get("scope") or ""
                        cand_symbol = f"{cand_scope}.{cand['name']}" if cand_scope else cand["name"]
                        relationships.append({
                            "source_file": file_path,
                            "source_symbol": source_symbol,
                            "target_file": cand["file_path"],
                            "target_symbol": cand_symbol,
                            "target_symbol_id": cand["id"],
                            "type": "calls",
                            "line": line,
                            "status": "ambiguous",
                        })
                elif len(candidates) == 1:
                    cand = candidates[0]
                    cand_scope = cand.get("scope") or ""
                    cand_symbol = f"{cand_scope}.{cand['name']}" if cand_scope else cand["name"]
                    relationships.append({
                        "source_file": file_path,
                        "source_symbol": source_symbol,
                        "target_file": cand["file_path"],
                        "target_symbol": cand_symbol,
                        "target_symbol_id": cand["id"],
                        "type": "calls",
                        "line": line,
                        "status": "ambiguous",
                    })
                else:
                    relationships.append({
                        "source_file": file_path,
                        "source_symbol": source_symbol,
                        "target_file": None,
                        "target_symbol": name,
                        "target_symbol_id": None,
                        "type": "calls",
                        "line": line,
                        "status": "unresolved",
                    })
                continue

            # Case C: Un-dotted call (e.g. run(), Crawler(), helper(), len())
            # 1. Local same-file function or class definition.
            local_defs = context.symbols_by_file_and_name.get((file_path, name), [])
            if local_defs:
                target_sym = local_defs[0]
                relationships.append({
                    "source_file": file_path,
                    "source_symbol": source_symbol,
                    "target_file": file_path,
                    "target_symbol": name,
                    "target_symbol_id": target_sym["id"],
                    "type": "calls",
                    "line": line,
                    "status": "resolved",
                })
                continue

            # 2. Imported function or class.
            if name in import_map:
                mapping = import_map[name]
                if mapping["status"] == "resolved" and mapping["target_file"]:
                    relationships.append({
                        "source_file": file_path,
                        "source_symbol": source_symbol,
                        "target_file": mapping["target_file"],
                        "target_symbol": mapping["target_symbol"],
                        "target_symbol_id": mapping.get("target_symbol_id"),
                        "type": "calls",
                        "line": line,
                        "status": "resolved",
                    })
                else:
                    relationships.append({
                        "source_file": file_path,
                        "source_symbol": source_symbol,
                        "target_file": None,
                        "target_symbol": name,
                        "target_symbol_id": None,
                        "type": "calls",
                        "line": line,
                        "status": "unresolved",
                    })
                continue

            # 3. Built-in or unresolvable call.
            relationships.append({
                "source_file": file_path,
                "source_symbol": source_symbol,
                "target_file": None,
                "target_symbol": name,
                "target_symbol_id": None,
                "type": "calls",
                "line": line,
                "status": "unresolved",
            })


class RelationshipResolver:
    """Coordinates semantic relationship resolution across repository files."""

    def __init__(
        self,
        store: SQLiteStore,
        project_root: str | Path | None = None,
    ):
        self.store = store
        self.project_root = Path(project_root).resolve() if project_root else None
        self.adapters: dict[str, LanguageRelationshipAdapter] = {
            "python": PythonRelationshipResolver(),
        }
        self.fallback_adapter = FallbackLanguageResolver()

    def _get_adapter(self, language: str) -> LanguageRelationshipAdapter:
        return self.adapters.get(language, self.fallback_adapter)

    def resolve_all(self) -> list[dict]:
        """Resolve all relationships for the entire repository."""
        context = ResolutionContext(self.store, self.project_root)
        all_relationships: list[dict] = []

        with self.store.connection:
            self.store.connection.execute("DELETE FROM relationships")

        for file_path, language in context.files.items():
            adapter = self._get_adapter(language)
            file_rels = adapter.resolve_file(file_path, context)
            all_relationships.extend(file_rels)

        self.store.store_relationships(all_relationships)
        return all_relationships

    def resolve_files(self, file_paths: set[str] | list[str]) -> list[dict]:
        """Incrementally resolve relationships for a subset of repository files."""
        if not file_paths:
            return []

        context = ResolutionContext(self.store, self.project_root)
        resolved: list[dict] = []

        self.store.delete_relationships_for_files(file_paths)

        for file_path in file_paths:
            language = context.files.get(file_path, "unknown")
            adapter = self._get_adapter(language)
            file_rels = adapter.resolve_file(file_path, context)
            resolved.extend(file_rels)

        self.store.store_relationships(resolved)
        return resolved

    def find_affected_files(self, changed_files: set[str]) -> set[str]:
        """Find files directly modified or referencing any modified/deleted files."""
        if not changed_files:
            return set()

        affected = set(changed_files)
        placeholders = ",".join("?" for _ in changed_files)
        params = list(changed_files)

        rows = self.store.connection.execute(
            f"SELECT DISTINCT source_file FROM relationships WHERE target_file IN ({placeholders})",
            params,
        ).fetchall()

        for row in rows:
            affected.add(row["source_file"])

        return affected
