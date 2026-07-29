from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from sentia_sidecar.repository import (
    MAX_CONTEXT_CHARS,
    MAX_SELECTED_FILES,
    ReadMode,
    RepositoryContext,
    RepositoryFile,
    RepositoryManifest,
    build_selected_repository_context,
)

CONTENT_RETRIEVAL_TIMEOUT_SECONDS = 15.0
DEFAULT_SEARCH_LIMIT = 12
MAX_SEARCH_LIMIT = 20

_STOP_WORDS = {
    "about",
    "and",
    "are",
    "can",
    "codebase",
    "does",
    "exactly",
    "files",
    "for",
    "from",
    "functions",
    "give",
    "how",
    "into",
    "job",
    "pair",
    "project",
    "quick",
    "report",
    "repository",
    "that",
    "the",
    "them",
    "this",
    "those",
    "what",
    "where",
    "which",
    "with",
    "you",
}
_ALIASES = {
    "agent": {"ai", "graph", "llm", "provider", "router"},
    "agents": {"ai", "graph", "llm", "provider", "router"},
    "claude": {"anthropic"},
    "gemini": {"generativeai", "google"},
    "model": {"ai", "llm", "provider", "router"},
    "models": {"ai", "llm", "provider", "router"},
}


class RepositoryAgentTools:
    """Read-only Agent SDK tools backed by Sentia's sanitized repository snapshot."""

    def __init__(self, manifest: RepositoryManifest) -> None:
        self._manifest = manifest
        self._reads: dict[str, ReadMode] = {}
        self._content_chars = 0

        search_tool = tool(
            "search_repository",
            (
                "Search Sentia's cached repository snapshot by path, role, imports, and symbol "
                "outline. Use focused technical terms and provider aliases before reading files."
            ),
            {"query": str, "limit": int},
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )(self._search_tool)
        read_tool = tool(
            "read_repository_file",
            (
                "Read one eligible repository file through Sentia's cache and secret-redaction "
                "layer. Use mode 'outline' for symbols/docstrings or 'full' for implementation."
            ),
            {"path": str, "mode": str},
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )(self._read_tool)
        self.server = create_sdk_mcp_server(
            name="sentia_repository",
            version="1.0.0",
            tools=[search_tool, read_tool],
        )

    @property
    def read_requests(self) -> tuple[tuple[str, ReadMode], ...]:
        return tuple(self._reads.items())

    async def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
        normalized_limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        terms = _expanded_terms(query)
        ranked = sorted(
            self._manifest.files.values(),
            key=lambda item: (
                -_metadata_relevance(item, terms),
                item.priority,
                item.path.lower(),
            ),
        )
        matches = [item for item in ranked if _metadata_relevance(item, terms) > 0]
        if not matches:
            matches = ranked
        return "\n".join(
            json.dumps(
                {
                    "path": item.path,
                    "role": item.role,
                    "bytes": item.size,
                    "outline": item.structure,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            for item in matches[:normalized_limit]
        )

    async def read(self, path: str, mode: str) -> str:
        normalized_path = path.strip()
        if normalized_path not in self._manifest.files:
            raise ValueError("The requested path is not in Sentia's eligible-file snapshot.")
        if mode not in {"outline", "full"}:
            raise ValueError("Read mode must be 'outline' or 'full'.")
        normalized_mode: ReadMode = "outline" if mode == "outline" else "full"
        existing = self._reads.get(normalized_path)
        if existing is not None and (existing == "full" or normalized_mode == "outline"):
            return "This file is already available in the agent context."
        if existing is None and len(self._reads) >= MAX_SELECTED_FILES:
            raise ValueError(
                f"The repository read limit is {MAX_SELECTED_FILES} files per question."
            )

        async with asyncio.timeout(CONTENT_RETRIEVAL_TIMEOUT_SECONDS):
            context = await asyncio.to_thread(
                build_selected_repository_context,
                self._manifest,
                [(normalized_path, normalized_mode)],
            )
        next_chars = self._content_chars + len(context.prompt)
        if next_chars > MAX_CONTEXT_CHARS:
            raise ValueError(
                "The repository evidence budget is full; answer from the files already read."
            )
        self._reads[normalized_path] = normalized_mode
        self._content_chars = next_chars
        return context.prompt

    def build_evidence_context(self) -> RepositoryContext:
        if not self._reads:
            raise ValueError("Claude did not read any repository files before answering.")
        return build_selected_repository_context(self._manifest, list(self._reads.items()))

    async def _search_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        limit = arguments.get("limit", DEFAULT_SEARCH_LIMIT)
        if not isinstance(query, str) or not query.strip():
            return _tool_error("Enter at least one repository search term.")
        if not isinstance(limit, int):
            return _tool_error("Search limit must be an integer.")
        return _tool_text(await self.search(query, limit))

    async def _read_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        mode = arguments.get("mode")
        if not isinstance(path, str) or not isinstance(mode, str):
            return _tool_error("A snapshot path and read mode are required.")
        try:
            content = await self.read(path, mode)
        except (TimeoutError, ValueError) as error:
            return _tool_error(str(error))
        return _tool_text(content)


def repository_snapshot_summary(manifest: RepositoryManifest) -> str:
    root_files = [path for path in manifest.files if "/" not in path]
    directories = sorted({path.partition("/")[0] for path in manifest.files if "/" in path})
    return (
        f"WORKSPACE ROOT: {manifest.root.name}\n"
        f"CACHED ELIGIBLE FILES: {manifest.files_scanned}\n"
        f"CACHE SOURCE: {manifest.cache_source}\n"
        f"ROOT FILES: {', '.join(root_files) or 'none'}\n"
        f"TOP-LEVEL DIRECTORIES: {', '.join(directories) or 'none'}\n"
        f"MARKDOWN CANDIDATES: {', '.join(manifest.description_paths) or 'none'}"
    )


def _expanded_terms(query: str) -> set[str]:
    terms = {
        term
        for term in re.findall(r"[a-z0-9_]+", query.lower())
        if len(term) >= 2 and term not in _STOP_WORDS
    }
    expanded = set(terms)
    for term in terms:
        expanded.update(_ALIASES.get(term, set()))
    return expanded


def _metadata_relevance(item: RepositoryFile, terms: set[str]) -> int:
    if not terms:
        return 0
    path = item.path.lower()
    path_tokens = set(re.findall(r"[a-z0-9]+", path))
    outline = item.structure.lower()
    outline_tokens = set(re.findall(r"[a-z0-9]+", outline))
    score = 0
    for term in terms:
        if term in path_tokens:
            score += 8
        elif term in path:
            score += 3
        if term in outline_tokens:
            score += 4
        elif term in outline:
            score += 1
    return score


def _tool_text(value: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": value}]}


def _tool_error(value: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": value}], "is_error": True}
