from pathlib import Path

import pytest
from sentia_sidecar.repository import (
    MAX_CONTEXT_CHARS,
    RepositoryError,
    build_repository_manifest,
    build_selected_repository_context,
    expand_repository_selection,
)


def test_repository_context_prioritizes_code_and_excludes_sensitive_paths(tmp_path: Path) -> None:
    fake_secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"  # pragma: allowlist secret
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".vite" / "deps").mkdir(parents=True)
    (tmp_path / "generated").mkdir()
    (tmp_path / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Acme\nA small service.\n", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text(
        f'from fastapi import FastAPI\n\napi_key = "{fake_secret}"\napp = FastAPI()\n',
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / "node_modules" / "ignored.js").write_text("throw new Error()\n", encoding="utf-8")
    (tmp_path / ".vite" / "deps" / "react.js").write_text(
        "export const generated = true\n",
        encoding="utf-8",
    )
    (tmp_path / "generated" / "ignored.py").write_text("raise RuntimeError()\n", encoding="utf-8")

    manifest = build_repository_manifest(str(tmp_path))
    context = build_selected_repository_context(manifest, [("src/main.py", "full")])

    assert "README.md" not in context.included_ranges
    assert "src/main.py" in context.included_ranges
    assert "generated/ignored.py" not in manifest.files
    assert ".env" not in context.prompt
    assert "node_modules" not in context.prompt
    assert not any(".vite" in path for path in manifest.files)
    assert "FastAPI" in context.prompt
    assert fake_secret not in context.prompt
    assert "[REDACTED: potential secret]" in context.prompt


def test_repository_context_rejects_empty_workspace(tmp_path: Path) -> None:
    with pytest.raises(RepositoryError, match="No supported"):
        build_repository_manifest(str(tmp_path))


def test_outline_reads_docstrings_and_signatures_without_function_bodies(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        '"""Service module summary."""\n\n'
        "def calculate_total(value: int) -> int:\n"
        '    """Return a transformed total."""\n'
        "    internal_secret = value * 1000\n"
        "    return internal_secret\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))

    context = build_selected_repository_context(manifest, [("service.py", "outline")])

    assert "Service module summary" in context.prompt
    assert "def calculate_total" in context.prompt
    assert "Return a transformed total" in context.prompt
    assert "internal_secret" not in context.prompt
    assert len(context.prompt) <= MAX_CONTEXT_CHARS + 200


def test_manifest_excludes_nested_virtualenv_and_prioritizes_project_markdown(
    tmp_path: Path,
) -> None:
    dependency_docs = (
        tmp_path / "backend" / "env" / "lib" / "python3.13" / "site-packages" / "langsmith" / "cli"
    )
    dependency_docs.mkdir(parents=True)
    (dependency_docs / "README.md").write_text(
        "# LangSmith CLI\nThird-party dependency documentation.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "PRODUCT_VISION.md").write_text(
        "# Autonomous Product Manager\nThe actual project description.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "SYSTEM_DESIGN.md").write_text(
        "# System design\nProject architecture.\n",
        encoding="utf-8",
    )
    (tmp_path / "backend" / "manage.py").write_text(
        "def main() -> None:\n    pass\n",
        encoding="utf-8",
    )

    manifest = build_repository_manifest(str(tmp_path))
    context = build_selected_repository_context(
        manifest,
        [("backend/env/lib/python3.13/site-packages/langsmith/cli/README.md", "full")],
    )

    assert not any("/env/" in f"/{path}" for path in manifest.files)
    assert manifest.description_paths[0] == "PRODUCT_VISION.md"
    assert "WORKSPACE ROOT" in manifest.prompt
    assert "MARKDOWN CANDIDATES: PRODUCT_VISION.md, docs/SYSTEM_DESIGN.md" in manifest.prompt
    assert "backend/manage.py" in context.selected_files
    assert "PRODUCT_VISION.md" in context.selected_files
    assert "LangSmith" not in context.prompt


def test_overview_shortlist_adds_code_and_manifest_to_readme_selection(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# Product\nDocumentation.\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"product"}\n', encoding="utf-8")
    (tmp_path / "src" / "main.ts").write_text(
        "export function startApp(): void {}\n", encoding="utf-8"
    )
    manifest = build_repository_manifest(str(tmp_path))

    selection = expand_repository_selection(
        manifest,
        [("README.md", "full")],
        "What is this codebase about?",
    )

    assert ("README.md", "full") in selection
    assert ("package.json", "full") in selection
    assert ("src/main.ts", "outline") in selection


def test_targeted_shortlist_does_not_force_readme(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# Product\nDocumentation.\n", encoding="utf-8")
    (tmp_path / "src" / "auth_service.py").write_text(
        "def authenticate_user() -> bool:\n    return True\n", encoding="utf-8"
    )
    manifest = build_repository_manifest(str(tmp_path))

    selection = expand_repository_selection(
        manifest,
        [("src/auth_service.py", "full")],
        "How does auth_service authenticate a user?",
    )

    assert selection == [("src/auth_service.py", "full")]
    assert "CACHED PROJECT TREE" in manifest.prompt
    assert "src/auth_service.py" in manifest.prompt


def test_question_terms_expand_shortlist_with_exact_layout_file(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "README.md").write_text("# Product\n", encoding="utf-8")
    (tmp_path / "app" / "layout.ts").write_text(
        "export const layoutColor = '#ffffff';\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))

    selection = expand_repository_selection(
        manifest,
        [("README.md", "full")],
        "I wanted to change the layout. What file do I need to work on?",
    )

    assert ("app/layout.ts", "full") in selection
