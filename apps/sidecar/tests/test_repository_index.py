from __future__ import annotations

from pathlib import Path

from sentia_sidecar.repository import build_repository_manifest
from sentia_sidecar.repository_index import RepositoryIndex, extract_source


def test_python_extraction_marks_local_calls_as_best_effort_until_resolved() -> None:
    symbols, edges = extract_source(
        "service.py",
        """import os

def helper() -> None:
    pass

def run() -> None:
    helper()
    os.getcwd()
""",
    )

    assert [item.qualified_name for item in symbols] == ["helper", "run"]
    assert {(item.target_name, item.edge_type) for item in edges} >= {
        ("os", "import"),
        ("helper", "call"),
        ("os.getcwd", "call"),
    }


def test_index_persists_fts_and_rebuilds_only_changed_files(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        """def helper() -> str:
    return \"hello repository\"

def run() -> str:
    return helper()
""",
        encoding="utf-8",
    )
    manifest = build_repository_manifest(str(tmp_path))
    index = RepositoryIndex(tmp_path)

    initial = index.update(manifest)
    hits = index.search("repository")
    graph = index.graph(path="service.py")
    unchanged = index.update(manifest, ["service.py"])

    assert initial.files_indexed == 1
    assert hits[0].path == "service.py"
    assert [node.label for node in graph.nodes] == ["service.py"]
    assert [item.name for item in graph.nodes[0].functions] == ["helper", "run"]
    assert unchanged.files_indexed == 0

    source.write_text("def changed() -> str:\n    return 'updated'\n", encoding="utf-8")
    refreshed = index.update(build_repository_manifest(str(tmp_path)), ["service.py"])

    assert refreshed.files_indexed == 1
    assert index.search("updated")[0].path == "service.py"


def test_index_removes_deleted_files(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("def present() -> None:\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# fixture\n", encoding="utf-8")
    index = RepositoryIndex(tmp_path)
    index.update(build_repository_manifest(str(tmp_path)))
    source.unlink()
    manifest = build_repository_manifest(str(tmp_path))

    stats = index.update(manifest, ["service.py"])

    assert stats.files_removed == 1
    assert index.search("present") == []
