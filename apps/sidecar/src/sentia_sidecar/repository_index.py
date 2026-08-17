# ruff: noqa: E501
"""Persistent, local repository index.

The index intentionally uses SQLite as the source of truth.  Syntax extraction
is deterministic; language adapters may emit unresolved edges, but must never
pretend a syntactic reference was semantically resolved.
"""

from __future__ import annotations

import ast
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sentia_sidecar.repository import RepositoryManifest

EdgeType = Literal["import", "call"]
Resolution = Literal["exact", "best_effort", "unresolved"]


@dataclass(frozen=True)
class ExtractedSymbol:
    qualified_name: str
    name: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ExtractedEdge:
    source_qualified_name: str | None
    target_name: str
    edge_type: EdgeType
    resolution: Resolution
    line: int


@dataclass(frozen=True)
class IndexStats:
    revision: int
    files_indexed: int
    files_removed: int


@dataclass(frozen=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    kind: str
    path: str
    summary: str
    functions: tuple["GraphFunction", ...]
    function_ranges: tuple["GraphFunction", ...]


@dataclass(frozen=True)
class GraphFunction:
    name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    edge_type: EdgeType
    resolution: Resolution


@dataclass(frozen=True)
class GraphSlice:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class RepositoryIndex:
    """A workspace-scoped SQLite/FTS index stored under ``.sentia``."""

    def __init__(self, workspace_path: str | Path) -> None:
        self.root = Path(workspace_path).expanduser().resolve()
        self.path = self.root / ".sentia" / "repository-index.db"

    def update(
        self,
        manifest: RepositoryManifest,
        changed_paths: list[str] | None = None,
    ) -> IndexStats:
        if manifest.root != self.root:
            raise ValueError("Repository index workspace does not match the manifest.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            self._initialize(connection)
            known_paths = set(manifest.files)
            paths = (
                known_paths
                if changed_paths is None
                else set(changed_paths)
                | {path for path in known_paths if path not in self._file_paths(connection)}
            )
            removed = self._file_paths(connection) - known_paths
            for path in removed:
                self._delete_file(connection, path)

            indexed = 0
            for path in sorted(paths & known_paths):
                source = self._read_workspace_file(path)
                if source is None:
                    self._delete_file(connection, path)
                    continue
                content_hash = _hash(source)
                existing = connection.execute(
                    "SELECT content_hash FROM files WHERE path = ?", (path,)
                ).fetchone()
                if existing is not None and existing["content_hash"] == content_hash:
                    continue
                self._replace_file(
                    connection, path, source, manifest.files[path].role, content_hash
                )
                indexed += 1

            revision = self._bump_revision(connection)
        return IndexStats(revision=revision, files_indexed=indexed, files_removed=len(removed))

    def search(self, query: str, *, limit: int = 8) -> list[SearchHit]:
        if not self.path.exists() or not query.strip():
            return []
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT chunks.path, chunks.start_line, chunks.end_line, chunks.text,
                       bm25(chunks_fts, 8.0, 2.0) AS score
                FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (_fts_query(query), limit),
            ).fetchall()
        return [
            SearchHit(
                path=str(row["path"]),
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                text=str(row["text"]),
                score=float(row["score"]),
            )
            for row in rows
        ]

    def graph(self, *, path: str | None = None, depth: int = 1) -> GraphSlice:
        """Return a bounded file-level graph for the interactive UI."""
        if not self.path.exists():
            return GraphSlice((), ())
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            symbol_rows = connection.execute(
                "SELECT id, qualified_name, kind, path, start_line, end_line FROM symbols ORDER BY path, start_line"
            ).fetchall()
            paths = {str(row["path"]) for row in symbol_rows}
            if path is not None and path not in paths:
                return GraphSlice((), ())
            symbols_by_path: dict[str, list[sqlite3.Row]] = {}
            for row in symbol_rows:
                symbols_by_path.setdefault(str(row["path"]), []).append(row)
            edge_rows = connection.execute(
                """
                SELECT source.path AS source_path, target.path AS target_path,
                       edges.edge_type, edges.resolution
                FROM edges
                JOIN symbols AS source ON source.id = edges.source_symbol_id
                JOIN symbols AS target ON target.id = edges.target_symbol_id
                """
            ).fetchall()
            selected_paths = paths if path is None else {path}
            if path is not None and depth > 0:
                for row in edge_rows:
                    if row["source_path"] == path or row["target_path"] == path:
                        selected_paths.update({str(row["source_path"]), str(row["target_path"])})
            nodes = tuple(
                GraphNode(
                    f"file:{file_path}",
                    Path(file_path).name,
                    "module",
                    file_path,
                    _file_summary(symbols_by_path[file_path]),
                    tuple(
                        GraphFunction(
                            str(item["qualified_name"]),
                            int(item["start_line"]),
                            int(item["end_line"]),
                        )
                        for item in symbols_by_path[file_path][:3]
                    ),
                    tuple(
                        GraphFunction(
                            str(item["qualified_name"]),
                            int(item["start_line"]),
                            int(item["end_line"]),
                        )
                        for item in symbols_by_path[file_path]
                    ),
                )
                for file_path in sorted(selected_paths)
            )
            allowed = {node.id for node in nodes}
            seen_edges: set[tuple[str, str, str, str]] = set()
            edges = tuple(
                GraphEdge(
                    f"file:{row['source_path']}",
                    f"file:{row['target_path']}",
                    row["edge_type"],
                    row["resolution"],
                )
                for row in edge_rows
                if f"file:{row['source_path']}" in allowed
                and f"file:{row['target_path']}" in allowed
                and not _duplicate_edge(seen_edges, row)
            )
        return GraphSlice(nodes, edges)

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (
              path TEXT PRIMARY KEY, role TEXT NOT NULL, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS symbols (
              id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
              qualified_name TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
              start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, symbol_hash TEXT NOT NULL,
              UNIQUE(path, qualified_name, start_line)
            );
            CREATE INDEX IF NOT EXISTS symbols_path_index ON symbols(path);
            CREATE TABLE IF NOT EXISTS edges (
              id INTEGER PRIMARY KEY, source_symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
              target_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
              target_name TEXT NOT NULL, edge_type TEXT NOT NULL CHECK(edge_type IN ('import', 'call')),
              resolution TEXT NOT NULL CHECK(resolution IN ('exact', 'best_effort', 'unresolved')),
              line INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS edges_source_index ON edges(source_symbol_id);
            CREATE TABLE IF NOT EXISTS chunks (
              id INTEGER PRIMARY KEY, symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
              path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE, start_line INTEGER NOT NULL,
              end_line INTEGER NOT NULL, content_hash TEXT NOT NULL, text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summaries (
              entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, source_hash TEXT NOT NULL,
              prompt_version TEXT NOT NULL, model TEXT NOT NULL, text TEXT NOT NULL,
              PRIMARY KEY(entity_type, entity_id, source_hash, prompt_version, model)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, path UNINDEXED, symbol UNINDEXED);
            """
        )

    def _replace_file(
        self, connection: sqlite3.Connection, path: str, source: str, role: str, content_hash: str
    ) -> None:
        self._delete_file(connection, path)
        connection.execute(
            "INSERT INTO files(path, role, content_hash) VALUES (?, ?, ?)",
            (path, role, content_hash),
        )
        symbols, edges = extract_source(path, source)
        symbol_ids: dict[str, int] = {}
        for symbol in symbols:
            cursor = connection.execute(
                """INSERT INTO symbols(path, qualified_name, name, kind, start_line, end_line, symbol_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    path,
                    symbol.qualified_name,
                    symbol.name,
                    symbol.kind,
                    symbol.start_line,
                    symbol.end_line,
                    _hash(f"{content_hash}:{symbol.qualified_name}:{symbol.start_line}"),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a symbol row id.")
            symbol_ids[symbol.qualified_name] = cursor.lastrowid
        for symbol in symbols:
            lines = source.splitlines()
            text = "\n".join(lines[symbol.start_line - 1 : symbol.end_line])
            self._insert_chunk(
                connection,
                path,
                symbol.start_line,
                symbol.end_line,
                text,
                symbol_ids[symbol.qualified_name],
            )
        if not symbols:
            self._insert_chunk(connection, path, 1, max(1, len(source.splitlines())), source, None)
        for edge in edges:
            target_id = symbol_ids.get(edge.target_name)
            resolution: Resolution = "exact" if target_id is not None else edge.resolution
            connection.execute(
                """INSERT INTO edges(source_symbol_id, target_symbol_id, target_name, edge_type, resolution, line)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    symbol_ids.get(edge.source_qualified_name or ""),
                    target_id,
                    edge.target_name,
                    edge.edge_type,
                    resolution,
                    edge.line,
                ),
            )

    @staticmethod
    def _insert_chunk(
        connection: sqlite3.Connection,
        path: str,
        start: int,
        end: int,
        text: str,
        symbol_id: int | None,
    ) -> None:
        cursor = connection.execute(
            """INSERT INTO chunks(symbol_id, path, start_line, end_line, content_hash, text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol_id, path, start, end, _hash(text), text),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a chunk row id.")
        connection.execute(
            "INSERT INTO chunks_fts(rowid, text, path, symbol) VALUES (?, ?, ?, ?)",
            (cursor.lastrowid, text, path, "" if symbol_id is None else str(symbol_id)),
        )

    @staticmethod
    def _file_paths(connection: sqlite3.Connection) -> set[str]:
        return {str(row[0]) for row in connection.execute("SELECT path FROM files")}

    @staticmethod
    def _delete_file(connection: sqlite3.Connection, path: str) -> None:
        chunk_ids = [
            row[0] for row in connection.execute("SELECT id FROM chunks WHERE path = ?", (path,))
        ]
        for chunk_id in chunk_ids:
            connection.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
        connection.execute("DELETE FROM files WHERE path = ?", (path,))

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM index_metadata WHERE key = 'revision'"
        ).fetchone()
        revision = int(row[0]) + 1 if row else 1
        connection.execute(
            "INSERT OR REPLACE INTO index_metadata(key, value) VALUES ('revision', ?)",
            (str(revision),),
        )
        return revision

    def _read_workspace_file(self, relative: str) -> str | None:
        candidate = (self.root / relative).resolve()
        if self.root not in candidate.parents or not candidate.is_file():
            return None
        try:
            return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None


def extract_source(path: str, source: str) -> tuple[list[ExtractedSymbol], list[ExtractedEdge]]:
    if path.endswith(".py"):
        return _extract_python(source)
    if path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return _extract_typescript(source)
    return [], []


def _extract_python(source: str) -> tuple[list[ExtractedSymbol], list[ExtractedEdge]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    symbols: list[ExtractedSymbol] = []
    edges: list[ExtractedEdge] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._symbol(node, "class")

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._symbol(node, "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._symbol(node, "function")

        def _symbol(
            self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: str
        ) -> None:
            qualified = ".".join([*stack, node.name])
            symbols.append(
                ExtractedSymbol(
                    qualified, node.name, kind, node.lineno, node.end_lineno or node.lineno
                )
            )
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Import(self, node: ast.Import) -> None:
            source_symbol = ".".join(stack) or None
            for item in node.names:
                edges.append(
                    ExtractedEdge(source_symbol, item.name, "import", "best_effort", node.lineno)
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            source_symbol = ".".join(stack) or None
            module = "." * node.level + (node.module or "")
            for item in node.names:
                target = f"{module}.{item.name}".strip(".") or item.name
                edges.append(
                    ExtractedEdge(source_symbol, target, "import", "best_effort", node.lineno)
                )

        def visit_Call(self, node: ast.Call) -> None:
            source_symbol = ".".join(stack) or None
            target = _call_name(node.func)
            if target:
                edges.append(
                    ExtractedEdge(source_symbol, target, "call", "best_effort", node.lineno)
                )
            self.generic_visit(node)

    Visitor().visit(tree)
    return symbols, edges


def _extract_typescript(source: str) -> tuple[list[ExtractedSymbol], list[ExtractedEdge]]:
    symbols: list[ExtractedSymbol] = []
    edges: list[ExtractedEdge] = []
    current: str | None = None
    for number, line in enumerate(source.splitlines(), 1):
        match = re.search(
            r"(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)", line
        )
        if match:
            name = match.group(1)
            kind = "class" if "class" in match.group(0) else "function"
            symbols.append(ExtractedSymbol(name, name, kind, number, number))
            current = name
        for imported in re.findall(
            r"(?:import|export)\s+(?:[^'\"]+?\s+from\s+)?['\"]([^'\"]+)['\"]", line
        ):
            edges.append(ExtractedEdge(current, imported, "import", "best_effort", number))
        for call in re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", line):
            if call not in {"function", "if", "for", "while", "switch", "catch"}:
                edges.append(ExtractedEdge(current, call, "call", "best_effort", number))
    return symbols, edges


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = [node.attr]
        value = node.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_summary(symbols: list[sqlite3.Row]) -> str:
    functions = sum(1 for item in symbols if item["kind"] == "function")
    classes = sum(1 for item in symbols if item["kind"] == "class")
    parts = []
    if functions:
        parts.append(f"{functions} function{'s' if functions != 1 else ''}")
    if classes:
        parts.append(f"{classes} class{'es' if classes != 1 else ''}")
    return f"Contains {', '.join(parts) or 'indexed symbols'}."


def _duplicate_edge(seen: set[tuple[str, str, str, str]], row: sqlite3.Row) -> bool:
    key = (str(row["source_path"]), str(row["target_path"]), row["edge_type"], row["resolution"])
    if key in seen:
        return True
    seen.add(key)
    return False


def _fts_query(value: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]+", value)
    return " OR ".join(f'"{term}"' for term in terms) or '""'
