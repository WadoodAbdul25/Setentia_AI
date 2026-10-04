import hashlib
from pathlib import Path

import pytest
from sentia_sidecar.flow_graph import build_feature_flow
from sentia_sidecar.protocol import FlowRelationshipKind, FlowRootSelection
from sentia_sidecar.repository import (
    MAX_CONTEXT_CHARS,
    MAX_SELECTED_FILES,
    ReadMode,
    RepositoryError,
    RepositoryManifest,
    build_repository_manifest,
    build_selected_repository_context,
    expand_repository_selection,
    requires_implementation_evidence,
)
from sentia_sidecar.structural_index import StructuralIndex


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


def test_manifest_uses_exact_file_bytes_as_content_identity(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_bytes(b"def service():\r\n    return 1\r\n")

    manifest = build_repository_manifest(str(tmp_path))
    entry = manifest.files["service.py"]

    assert entry.size == len(source.read_bytes())
    assert entry.content_hash == hashlib.sha256(source.read_bytes()).hexdigest()


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
    assert len(context.prompt) <= MAX_CONTEXT_CHARS


@pytest.mark.parametrize("question", ["", "Read the code and tell me what features are present."])
def test_large_markdown_cannot_crowd_out_later_implementation_files(
    markdown_first_manifest: RepositoryManifest,
    question: str,
) -> None:
    manifest = markdown_first_manifest
    documents = ["README.md", "PRODUCT_VISION_V2.md", "SESSIONS.md"]
    code = [path for path in manifest.files if path.endswith(".py")]
    requested: list[tuple[str, ReadMode]] = [(path, "full") for path in documents + code]

    context = build_selected_repository_context(manifest, requested, question=question)

    assert context.files_read == 8
    assert set(context.selected_files) == set(documents + code)
    assert context.omitted_files == ()
    assert set(context.truncated_files) == set(documents + code)
    assert len(context.prompt) <= MAX_CONTEXT_CHARS
    assert "not a full repository audit" in context.prompt
    if question:
        assert list(context.selected_files[:5]) == code
    else:
        assert list(context.selected_files[:3]) == documents
    assert context.included_lines is not None
    for path in code:
        original_lines = (manifest.root / path).read_text(encoding="utf-8").splitlines()
        assert {1, 2} <= context.included_lines[path]
        for number in context.included_lines[path]:
            assert f"{number:>4}: {original_lines[number - 1]}" in context.prompt
    if question:
        assert min(len(context.included_lines[path]) for path in code) > max(
            len(context.included_lines[path]) for path in documents
        )


def test_short_files_release_their_unused_evidence_budget(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Tiny documentation\n", encoding="utf-8")
    (tmp_path / "service.py").write_text(
        "def run():\n    return True\n" + ("# implementation details " * 3 + "\n") * 170,
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))

    context = build_selected_repository_context(
        manifest,
        [("README.md", "full"), ("service.py", "full")],
    )

    assert context.selected_files == ("README.md", "service.py")
    assert len(context.prompt) > 3_800
    assert len(context.prompt) <= MAX_CONTEXT_CHARS
    assert context.truncated_files == ("service.py",)


def test_oversized_line_does_not_hide_following_code_and_citations_keep_original_numbers(
    tmp_path: Path,
) -> None:
    (tmp_path / "service.py").write_text(
        "# " + "large header " * 1_000 + "\ndef run():\n    return True\n",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))

    context = build_selected_repository_context(manifest, [("service.py", "full")])

    assert context.included_lines == {"service.py": frozenset({2, 3})}
    assert "   2: def run():" in context.prompt
    assert "large header" not in context.prompt
    assert context.truncated_files == ("service.py",)
    assert len(context.prompt) <= MAX_CONTEXT_CHARS


def test_unreadable_selected_files_are_reported_as_omitted(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Fixture\n", encoding="utf-8")
    removed = tmp_path / "service.py"
    removed.write_text("def run():\n    return True\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    removed.unlink()

    context = build_selected_repository_context(
        manifest,
        [("README.md", "full"), ("service.py", "full")],
    )

    assert context.omitted_files == ("service.py",)
    assert context.files_read == 1


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Read the code and tell me what features are present.", True),
        ("Inspect the source to see what actually works.", True),
        ("What features are implemented?", True),
        ("Read the code and compare it with the README roadmap.", True),
        ("Read the code; use the README too, but only tell me about features.", True),
        ("Only read the README and list its features.", False),
        ("List features according to PRODUCT_VISION_V2.md.", False),
        ("Summarize the planned features in the documentation.", False),
        ("Don't read the code, just use README.md.", False),
        ("Without reading source code, what does the README say?", False),
        ("What is this project?", False),
    ],
)
def test_implementation_intent_respects_user_evidence_scope(question: str, expected: bool) -> None:
    assert requires_implementation_evidence(question) is expected


def test_implementation_request_makes_room_for_code_in_an_eight_document_shortlist(
    tmp_path: Path,
) -> None:
    documents = [f"plan_{index}.md" for index in range(MAX_SELECTED_FILES)]
    for path in documents:
        (tmp_path / path).write_text("# Future features\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("def start():\n    return True\n", encoding="utf-8")
    (tmp_path / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    requested: list[tuple[str, ReadMode]] = [(path, "full") for path in documents]

    selection = expand_repository_selection(manifest, requested, "Read the code and list features.")

    assert len(selection) == MAX_SELECTED_FILES
    assert len({path for path, _ in selection}) == MAX_SELECTED_FILES
    assert selection[:2] == [("main.py", "full"), ("service.py", "full")]


def test_implementation_request_reads_bodies_instead_of_only_signatures(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return 'working'\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    question = "Read the code and tell me which features work."

    selection = expand_repository_selection(manifest, [("service.py", "outline")], question)
    context = build_selected_repository_context(manifest, selection, question=question)

    assert selection == [("service.py", "full")]
    assert "return 'working'" in context.prompt


@pytest.mark.parametrize(
    "question",
    [
        "Only read the README and list its features.",
        "Summarize the features documented in README.md.",
        "What is this project according to README.md?",
    ],
)
def test_documentation_only_request_does_not_expand_into_code(
    tmp_path: Path,
    question: str,
) -> None:
    (tmp_path / "README.md").write_text("# Product features\n", encoding="utf-8")
    (tmp_path / "features.py").write_text("def features():\n    return True\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))

    selection = expand_repository_selection(
        manifest,
        [("README.md", "full"), ("features.py", "full")],
        question,
    )
    context = build_selected_repository_context(manifest, selection, question=question)

    assert selection == [("README.md", "full")]
    assert context.selected_files == ("README.md",)
    assert "def features" not in context.prompt


def test_documentation_only_request_cannot_fall_back_to_code(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
    manifest = build_repository_manifest(str(tmp_path))
    question = "Only read the README and list features."

    selection = expand_repository_selection(manifest, [("service.py", "full")], question)

    assert selection == []
    with pytest.raises(RepositoryError, match="no documentation"):
        build_selected_repository_context(manifest, selection, question=question)


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


def test_manifest_relationships_feed_an_end_to_end_feature_flow(tmp_path: Path) -> None:
    (tmp_path / "helpers.py").write_text(
        "def create_session() -> str:\n    return 'session'\n",
        encoding="utf-8",
    )
    (tmp_path / "auth.py").write_text(
        "from helpers import create_session\n\ndef login() -> str:\n    return create_session()\n",
        encoding="utf-8",
    )

    manifest = build_repository_manifest(str(tmp_path), repository_revision=4)
    index = StructuralIndex(
        manifest.root,
        manifest.repository_revision,
        manifest.entities,
        manifest.relationships,
    )
    login = index.search_entities("login")[0]
    create_session = index.search_entities("create_session")[0]

    assert len(manifest.relationships) == 1
    assert manifest.relationships[0].source_entity_id == login.entity_id
    assert manifest.relationships[0].target_entity_id == create_session.entity_id
    assert manifest.relationships[0].kind == FlowRelationshipKind.CALLS

    graph = build_feature_flow(
        index,
        FlowRootSelection(
            repository_revision=4,
            root_entity_ids=[login.entity_id],
            rationale="Login is the feature entry point.",
        ),
        question="How does login create a session?",
    )

    assert {node.entity_id for node in graph.nodes} >= {
        login.entity_id,
        create_session.entity_id,
    }
    assert len(graph.edges) == 1
