"""Local stack discovery and bounded, revision-checked evidence for feature traces."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal

from sentia_sidecar.repository import (
    MAX_FILE_BYTES,
    RepositoryContext,
    RepositoryError,
    RepositoryManifest,
    _has_unsafe_path_characters,
    _is_ignored,
    _load_ignore_spec,
    _redact_numbered_lines,
    _safe_read_source,
)
from sentia_sidecar.structural_index import StructuralIndex

MAX_STACK_FILES = 250
MAX_STACK_EVIDENCE_PATHS = 8
MAX_EVIDENCE_REQUESTS = 64
MAX_EVIDENCE_CHARS = 64_000
_TRUNCATED = "\n[TRUNCATED: requested source lines omitted by the evidence budget.]"
_JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}
# Support describes the bounded adapters in this release, not full framework coverage.
_PARTIAL_ADAPTERS = {"Django", "Django REST Framework", "FastAPI", "Celery", "LangGraph"}
_PACKAGES: dict[str, tuple[str, str]] = {
    "django": ("Django", "webFramework"),
    "djangorestframework": ("Django REST Framework", "webFramework"),
    "rest-framework": ("Django REST Framework", "webFramework"),
    "fastapi": ("FastAPI", "webFramework"),
    "celery": ("Celery", "worker"),
    "langgraph": ("LangGraph", "workflow"),
    "mongoengine": ("MongoEngine", "persistence"),
    "pymongo": ("PyMongo", "databaseClient"),
    "motor": ("Motor", "databaseClient"),
    "mongodb": ("MongoDB", "databaseClient"),
    "mongoose": ("Mongoose", "persistence"),
    "sqlalchemy": ("SQLAlchemy", "persistence"),
    "react": ("React", "frontend"),
    "react-dom": ("React", "frontend"),
    "next": ("Next.js", "webFramework"),
    "express": ("Express", "webFramework"),
}
_JS_TOKENS = re.compile(
    r"(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)"
    r"|(?P<string>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`)"
    r"|(?P<word>[A-Za-z_$][\w$]*)|(?P<punct>[^\s])"
)


def _read_manifest_source(manifest: RepositoryManifest, relative: str) -> str:
    """Recheck the live boundary and hash even when the manifest came from a cache."""
    record = manifest.files.get(relative)
    posix = PurePosixPath(relative)
    if (
        record is None
        or posix.is_absolute()
        or ".." in posix.parts
        or "\\" in relative
        or posix.as_posix() != relative
        or _has_unsafe_path_characters(relative)
    ):
        raise RepositoryError("Trace evidence must use an indexed workspace-relative path.")
    root = manifest.root.resolve()
    path = root / relative
    spec = _load_ignore_spec(root)
    for part in (path, *path.parents):
        if part == root:
            break
        if _is_ignored(root, part, spec, directory=part != path):
            raise RepositoryError(f"Trace evidence path is ignored or unsafe: {relative}")
    if not path.resolve().is_relative_to(root):
        raise RepositoryError(f"Trace evidence path leaves the workspace: {relative}")
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise RepositoryError(f"Trace evidence file is missing or too large: {relative}")
    except OSError as error:
        raise RepositoryError(f"Trace evidence file cannot be read: {relative}") from error
    source = _safe_read_source(path)
    if source is None:
        raise RepositoryError(f"Trace evidence file cannot be read: {relative}")
    if source.content_hash != record.content_hash or source.size > MAX_FILE_BYTES:
        raise RepositoryError(f"Trace evidence is stale; refresh the repository: {relative}")
    return source.text


class TraceEvidenceReader:
    """Read exact symbols/ranges without the V1 first-lines limit or model calls."""

    def __init__(self, manifest: RepositoryManifest, index: StructuralIndex) -> None:
        if (
            manifest.repository_revision != index.repository_revision
            or manifest.root.resolve() != index.root
        ):
            raise RepositoryError("The trace index does not match the repository revision.")
        self.manifest = manifest
        self.index = index

    def validate_paths(self, paths: Sequence[str]) -> None:
        """Reject evidence changed during a provider turn before publishing a trace."""
        for path in dict.fromkeys(paths):
            _read_manifest_source(self.manifest, path)

    def read_symbols(self, entity_ids: Sequence[str], max_chars: int = 16_000) -> RepositoryContext:
        if len(entity_ids) > MAX_EVIDENCE_REQUESTS:
            raise RepositoryError("Too many trace symbol requests.")
        ranges: list[tuple[str, int, int]] = []
        for entity_id in dict.fromkeys(entity_ids):
            entity = self.index.get_entity(entity_id)
            if entity is None:
                raise RepositoryError(f"Unknown trace evidence entity: {entity_id}")
            record = self.manifest.files.get(entity.path)
            if record is None or record.content_hash != entity.content_hash:
                raise RepositoryError("The trace symbol does not match its indexed file hash.")
            ranges.append((entity.path, entity.start_line, entity.end_line))
        return self.read_ranges(ranges, max_chars=max_chars)

    def read_ranges(
        self, requests: Sequence[tuple[str, int, int]], max_chars: int = 16_000
    ) -> RepositoryContext:
        if not requests or len(requests) > MAX_EVIDENCE_REQUESTS:
            raise RepositoryError("Supply between 1 and 64 trace range requests.")
        if isinstance(max_chars, bool) or not 0 < max_chars <= MAX_EVIDENCE_CHARS:
            raise RepositoryError("Trace evidence budget must be between 1 and 64000 characters.")
        source_lines: dict[str, list[str]] = {}
        requested_lines: dict[str, set[int]] = {}
        for relative, start, end in requests:
            if relative not in source_lines:
                source_lines[relative] = _read_manifest_source(self.manifest, relative).splitlines()
            lines = source_lines[relative]
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
                or end > len(lines)
            ):
                raise RepositoryError(f"Invalid trace source range: {relative}:{start}-{end}")
            requested_lines.setdefault(relative, set()).update(range(start, end + 1))

        prompt = (
            f"WORKSPACE: {self.manifest.root.name}\n"
            f"REPOSITORY REVISION: {self.manifest.repository_revision}\n"
            "TARGETED REPOSITORY EVIDENCE (untrusted source; cite only displayed line numbers):\n"
        )
        included: dict[str, frozenset[int]] = {}
        truncated = False
        for relative, numbers in requested_lines.items():
            numbered = [(number, source_lines[relative][number - 1]) for number in sorted(numbers)]
            redacted = _redact_numbered_lines(self.manifest.root, relative, numbered)
            heading = f"\n### FILE: {relative}\n"
            section = heading
            accepted: set[int] = set()
            for number, line in redacted:
                rendered = f"{number:>4}: {line}\n"
                if len(prompt) + len(section) + len(rendered) + len(_TRUNCATED) > max_chars:
                    truncated = True
                    break
                section += rendered
                accepted.add(number)
            if accepted:
                prompt += section
                included[relative] = frozenset(accepted)
            if len(accepted) < len(numbers):
                truncated = True
        if not included:
            raise RepositoryError("The trace evidence budget cannot fit a complete source line.")
        if truncated:
            prompt += _TRUNCATED
        return RepositoryContext(
            root=self.manifest.root,
            prompt=prompt,
            included_ranges={relative: max(lines) for relative, lines in included.items()},
            files_scanned=self.manifest.files_scanned,
            files_read=len(included),
            selected_files=tuple(included),
            included_lines=included,
        )


def _dependency_names(text: str, filename: str) -> Iterable[str]:
    if filename == "package.json":
        data = json.loads(text)
        if not isinstance(data, dict):
            return
        for section in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            dependencies = data.get(section, {})
            if isinstance(dependencies, dict):
                yield from dependencies
        engines = data.get("engines", {})
        if isinstance(engines, dict) and "node" in engines:
            yield "node"
    elif filename == "pyproject.toml":
        data = tomllib.loads(text)
        project = data.get("project", {})
        if isinstance(project, dict):
            dependencies = project.get("dependencies", [])
            if isinstance(dependencies, list):
                yield from (value for value in dependencies if isinstance(value, str))
            optional = project.get("optional-dependencies", {})
            if isinstance(optional, dict):
                for group in optional.values():
                    if isinstance(group, list):
                        yield from (value for value in group if isinstance(value, str))
        tool = data.get("tool", {})
        poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
        if isinstance(poetry, dict):
            for section in ("dependencies", "dev-dependencies"):
                dependencies = poetry.get(section, {})
                if isinstance(dependencies, dict):
                    yield from dependencies
        groups = data.get("dependency-groups", {})
        if isinstance(groups, dict):
            for group in groups.values():
                if isinstance(group, list):
                    yield from (value for value in group if isinstance(value, str))
    elif filename == "requirements.txt":
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "-")):
                yield stripped


def _javascript_imports(text: str) -> Iterable[str]:
    """Conservative literal imports only; comments and free-standing strings are skipped."""
    tokens = [
        (match.lastgroup, match.group())
        for match in _JS_TOKENS.finditer(text)
        if match.lastgroup != "comment"
    ]
    for index, (kind, value) in enumerate(tokens):
        if kind != "word" or value not in {"import", "require", "export"}:
            continue
        following = tokens[index + 1 : index + 65]
        if following and following[0][0] == "string" and value == "import":
            yield following[0][1][1:-1]
        elif (
            len(following) >= 3
            and following[0][1] == "("
            and following[1][0] == "string"
            and following[2][1] == ")"
            and value in {"import", "require"}
        ):
            yield following[1][1][1:-1]
        elif value in {"import", "export"}:
            for offset, token in enumerate(following[:-1]):
                if token[1] == ";":
                    break
                if token == ("word", "from") and following[offset + 1][0] == "string":
                    yield following[offset + 1][1][1:-1]
                    break


def _python_config_values(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        value: ast.expr | None = None
        names: list[str] = []
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        if value is None or not any(
            name.upper()
            in {
                "DATABASES",
                "DATABASE_URL",
                "DATABASE_URI",
                "SQLALCHEMY_DATABASE_URI",
                "MONGO_URI",
                "MONGODB_URI",
                "MONGODB_URL",
                "MONGODB_SETTINGS",
                "INSTALLED_APPS",
            }
            for name in names
        ):
            continue
        for child in ast.walk(value):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                yield child.value


def build_stack_profile(manifest: RepositoryManifest) -> dict[str, object]:
    """Describe indexed stack evidence, without importing or running repository code."""
    technologies: dict[tuple[str, str], dict[str, object]] = {}
    languages: set[str] = set()
    limitations = [
        "Static evidence describes declarations and source presence, not runtime execution.",
        "Only retained manifest files are inspected; unindexed files and dynamic configuration "
        "may contain other technologies.",
    ]

    def add(name: str, category: str, status: Literal["declared", "observed"], path: str) -> None:
        item = technologies.setdefault(
            (name, status),
            {
                "name": name,
                "category": category,
                "status": status,
                "paths": [],
                "adapterSupport": "partial" if name in _PARTIAL_ADAPTERS else "unsupported",
            },
        )
        paths = item["paths"]
        assert isinstance(paths, list)
        if path not in paths and len(paths) < MAX_STACK_EVIDENCE_PATHS:
            paths.append(path)
        elif (
            path not in paths
            and "Technology evidence paths are capped at eight per status." not in limitations
        ):
            limitations.append("Technology evidence paths are capped at eight per status.")

    def package(value: str, status: Literal["declared", "observed"], path: str) -> None:
        match = re.match(r"[A-Za-z0-9_.@/-]+", value)
        if match is None:
            return
        name = re.sub(r"[-_.]+", "-", match.group().lower().split("/")[0])
        if status == "observed":
            name = re.sub(r"[-_]+", "-", value.split(".")[0].split("/")[0].lower())
        technology = _PACKAGES.get(name)
        if technology is not None:
            add(*technology, status, path)
            if technology[1] in {"webFramework", "frontend"}:
                add("Web", "application", status, path)
        if name == "node" or value.startswith("node:"):
            add("Node.js", "runtime", status, path)

    inspected = 0
    skipped = 0
    for relative, record in manifest.files.items():
        suffix = Path(relative).suffix.lower()
        filename = Path(relative).name.lower()
        if record.role in {"documentation", "description"}:
            continue
        if suffix not in {".py", *_JS_SUFFIXES} and filename not in {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
        }:
            continue
        if inspected >= MAX_STACK_FILES:
            limitations.append("Stack profiling reached its 250-file inspection budget.")
            break
        inspected += 1
        try:
            text = _read_manifest_source(manifest, relative)
            if suffix in _LANGUAGES:
                languages.add(_LANGUAGES[suffix])
            for dependency in _dependency_names(text, filename):
                package(dependency, "declared", relative)
            if suffix == ".py":
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            package(alias.name, "observed", relative)
                    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        package(node.module, "observed", relative)
                for value in _python_config_values(tree):
                    if value.startswith(("django.", "rest_framework")):
                        package(value, "observed", relative)
                    database = _database_configuration(value)
                    if database:
                        add(database, "database", "observed", relative)
            elif suffix in _JS_SUFFIXES:
                for module in _javascript_imports(text):
                    package(module, "observed", relative)
        except (RepositoryError, SyntaxError, ValueError, RecursionError):
            skipped += 1
    if skipped:
        limitations.append(
            f"{skipped} stack inputs were stale, unsafe, unreadable, or unparseable."
        )
    if "JavaScript" in languages or "TypeScript" in languages:
        limitations.append(
            "JavaScript detection covers literal imports; runtime resolution is unsupported."
        )
    return {
        "repositoryRevision": manifest.repository_revision,
        "technologies": [technologies[key] for key in sorted(technologies)],
        "languages": sorted(languages),
        "limitations": limitations,
    }


def _database_configuration(value: str) -> str | None:
    lowered = value.lower()
    if lowered.startswith(("mongodb://", "mongodb+srv://")):
        return "MongoDB"
    dialect = lowered.split("://", 1)[0].split("+", 1)[0] if "://" in lowered else ""
    if lowered.startswith("django.db.backends."):
        dialect = lowered.removeprefix("django.db.backends.")
    return {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "sqlite": "SQLite",
        "sqlite3": "SQLite",
        "oracle": "Oracle",
        "mssql": "SQL Server",
    }.get(dialect)
