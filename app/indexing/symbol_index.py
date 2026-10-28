
import ast
from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from app.indexing.language_detector import detect_file


# Tree-sitter grammar names for supported source languages.
PARSERS = {
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "java": "java",
    "go": "go",
    "rust": "rust",
    "css": "css",
}

SYMBOL_TYPES = {
    "function_definition": "function",
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "method_definition": "method",
    "class_definition": "class",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "function_item": "function",
    "struct_item": "struct",
    "trait_item": "trait",
}

IMPORT_TYPES = {
    "import_statement",
    "import_declaration",
    "use_declaration",
    "import_from_statement",
}

CALL_TYPES = {"call", "call_expression", "new_expression"}


def _python_index(source: str, file: str, line_offset: int = 0) -> dict:
    """Extract Python symbols, imports and calls using Python's AST."""
    result = {"symbols": [], "imports": [], "calls": []}

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        result["errors"] = [f"Python syntax error at line {exc.lineno}: {exc.msg}"]
        return result

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope: list[str] = []

        def visit_ClassDef(self, node):
            result["symbols"].append({
                "name": node.name,
                "type": "class",
                "language": "python",
                "file": file,
                "line": node.lineno + line_offset,
                "end_line": getattr(node, "end_lineno", node.lineno) + line_offset,
                "scope": ".".join(self.scope),
            })
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node):
            self._visit_function(node)

        def _visit_function(self, node):
            result["symbols"].append({
                "name": node.name,
                "type": "method" if self.scope and
                    any(s["type"] == "class" and
                        s["name"] == self.scope[-1]
                        for s in result["symbols"]) else "function",
                "language": "python",
                "file": file,
                "line": node.lineno + line_offset,
                "end_line": getattr(node, "end_lineno", node.lineno) + line_offset,
                "scope": ".".join(self.scope),
            })
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_Import(self, node):
            result["imports"].append({
                "text": ast.unparse(node),
                "file": file,
                "line": node.lineno + line_offset,
                "language": "python",
            })

        def visit_ImportFrom(self, node):
            result["imports"].append({
                "text": ast.unparse(node),
                "file": file,
                "line": node.lineno + line_offset,
                "language": "python",
            })

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                parts = []
                current = func
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                name = ".".join(reversed(parts))
            else:
                name = ast.unparse(func)

            result["calls"].append({
                "name": name,
                "file": file,
                "line": node.lineno + line_offset,
                "language": "python",
            })
            self.generic_visit(node)

    Visitor().visit(tree)
    return result


def _tree_sitter_index(
    source: str, language: str, file: str, line_offset: int = 0
) -> dict:
    """Extract common symbol, import and call nodes with Tree-sitter."""
    result = {"symbols": [], "imports": [], "calls": []}
    parser_name = PARSERS.get(language)

    if not parser_name:
        result["errors"] = [f"No parser configured for {language}"]
        return result

    try:
        parser = get_parser(parser_name)
        tree = parser.parse(source.encode("utf-8"))
    except Exception as exc:
        result["errors"] = [f"Could not parse {language}: {exc}"]
        return result

    source_bytes = source.encode("utf-8")

    def node_text(node) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode(
            "utf-8", errors="replace"
        )

    def visit(node, scope: list[str]):
        node_type = node.type

        if node_type in SYMBOL_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                # Fallback for grammars with different field layouts.
                name_node = next(
                    (child for child in node.children
                     if child.type in {"identifier", "type_identifier"}),
                    None,
                )

            name = node_text(name_node) if name_node else node_type
            symbol_type = SYMBOL_TYPES[node_type]

            result["symbols"].append({
                "name": name,
                "type": symbol_type,
                "language": language,
                "file": file,
                "line": node.start_point.row + 1 + line_offset,
                "end_line": node.end_point.row + 1 + line_offset,
                "scope": ".".join(scope),
            })

            # Traverse the symbol body with its name as the current scope.
            for child in node.children:
                visit(child, scope + [name])
            return

        if node_type in IMPORT_TYPES:
            result["imports"].append({
                "text": node_text(node),
                "file": file,
                "line": node.start_point.row + 1 + line_offset,
                "language": language,
            })

        if node_type in CALL_TYPES:
            target = node.child_by_field_name("function")
            if target is None:
                target = node.child_by_field_name("constructor")

            result["calls"].append({
                "name": node_text(target) if target else node_text(node),
                "file": file,
                "line": node.start_point.row + 1 + line_offset,
                "language": language,
            })

        for child in node.children:
            visit(child, scope)

    visit(tree.root_node, [])
    return result


def index_source(
    source: str, language: str, file: str, line_offset: int = 0
) -> dict:
    """Index a source string and return a consistent JSON-friendly structure."""
    if language == "python":
        return _python_index(source, file, line_offset)

    if language in PARSERS:
        return _tree_sitter_index(source, language, file, line_offset)

    return {
        "symbols": [],
        "imports": [],
        "calls": [],
        "errors": [f"Symbol indexing not yet supported for {language}"],
    }


def build_index(path: str | Path) -> dict:
    """Index one file, including inline JS and CSS inside HTML files."""
    path = Path(path)
    relative_file = str(path)
    result: dict[str, Any] = {
        "file": relative_file,
        "symbols": [],
        "imports": [],
        "calls": [],
        "errors": [],
    }

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        result["errors"].append(f"Could not read file: {exc}")
        return result

    detected = detect_file(path)
    language = detected.get("language", "unknown")

    if language == "html":
        # Index embedded script/style regions rather than treating HTML
        # markup as ordinary JavaScript or CSS.
        for region in detected.get("embedded_regions", []):
            embedded_language = region.get("language", "")
            embedded_source = region.get("source", "")
            start_line = region.get("start_line", 1)

            if embedded_language not in PARSERS:
                continue

            part = index_source(
                embedded_source,
                embedded_language,
                relative_file,
                line_offset=max(0, start_line - 1),
            )
            for key in ("symbols", "imports", "calls", "errors"):
                result[key].extend(part.get(key, []))
        return result

    indexed = index_source(source, language, relative_file)
    for key in ("symbols", "imports", "calls", "errors"):
        result[key].extend(indexed.get(key, []))

    return result
