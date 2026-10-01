from __future__ import annotations

from sentia_sidecar.repository import build_repository_manifest


def framework_facts(tmp_path, source):
    (tmp_path / "feature.py").write_text(source)
    manifest = build_repository_manifest(str(tmp_path))
    return [
        fact
        for fact in manifest.relationships
        if fact.provenance.extractor == "sentia.python.frameworks"
    ]


def test_tasks_need_import_backed_decorator_and_unique_binding(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from celery import shared_task as background
@background
def worker(value):
    return value
def dispatch(value):
    worker.delay(value)
def shadowed(worker):
    worker.delay(1)
""",
    )
    dispatches = [fact for fact in facts if fact.metadata.get("semantic") == "task_dispatch"]
    assert len(dispatches) == 1
    assert dispatches[0].asynchronous
    assert len(dispatches[0].evidence) >= 2


def test_fake_framework_names_do_not_create_edges(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
def shared_task(fn):
    return fn
@shared_task
def worker():
    pass
def dispatch():
    worker.delay()
""",
    )
    assert facts == []


def test_compilation_is_registration_not_invocation(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from langgraph.graph import StateGraph
def step(state):
    return state
def build():
    graph = StateGraph(dict)
    graph.add_node("step", step)
    graph.set_entry_point("step")
    return graph.compile()
""",
    )
    assert any(fact.metadata.get("semantic") == "workflow_registration" for fact in facts)
    assert not any(fact.metadata.get("semantic") == "workflow_invocation" for fact in facts)


def test_dynamic_and_duplicate_node_keys_are_not_resolved(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from langgraph.graph import StateGraph
def first(state):
    return state
def second(state):
    return state
def build(name):
    graph = StateGraph(dict)
    graph.add_node("same", first)
    graph.add_node("same", second)
    graph.add_node(name, first)
    graph.set_entry_point("same")
    return graph.compile()
""",
    )
    assert not any(fact.metadata.get("semantic") == "workflow_registration" for fact in facts)


def test_fastapi_route_keeps_mount_path_unknown(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from fastapi import APIRouter
router = APIRouter(prefix="/reports")
@router.post("/generate")
def submit(seed: str):
    return seed
""",
    )
    route = next(fact for fact in facts if fact.metadata.get("semantic") == "http_handler")
    assert route.metadata["route"] == "/generate"
    assert route.metadata["routerPrefix"] == "/reports"
    assert route.metadata["effectivePath"] is None


def test_raw_graph_invoke_does_not_prove_compiled_execution(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from langgraph.graph import StateGraph
def step(state):
    return state
def run(state):
    graph = StateGraph(dict)
    graph.add_node("step", step)
    graph.set_entry_point("step")
    return graph.invoke(state)
""",
    )
    assert not any(fact.metadata.get("semantic") == "workflow_invocation" for fact in facts)


def test_unknown_duplicate_action_keeps_node_ambiguous(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from langgraph.graph import StateGraph
def step(state):
    return state
def run(state, callback):
    graph = StateGraph(dict)
    graph.add_node("step", step)
    graph.add_node("step", callback)
    graph.set_entry_point("step")
    return graph.compile().invoke(state)
""",
    )
    assert not any(
        fact.metadata.get("semantic") in {"workflow_invocation", "workflow_registration"}
        for fact in facts
    )


def test_wildcard_import_does_not_override_unique_celery_binding(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from celery import shared_task
from custom import *
@shared_task
def worker():
    pass
def dispatch():
    worker.delay()
""",
    )
    assert not any(fact.metadata.get("semantic") == "task_dispatch" for fact in facts)


def test_drf_class_view_registration_reaches_concrete_http_method(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from django.urls import path
from rest_framework.views import APIView
class GenerateView(APIView):
    def post(self, request):
        return request.data
urlpatterns = [path("generate/", GenerateView.as_view())]
""",
    )
    handler = next(fact for fact in facts if fact.provenance.rule_id.endswith("class_view_handler"))
    assert handler.metadata["method"] == "POST"
    assert handler.metadata["route"] == "generate/"


def test_overridden_dispatch_is_not_assumed_to_use_standard_view_methods(tmp_path):
    facts = framework_facts(
        tmp_path,
        """
from django.urls import path
from rest_framework.views import APIView
class CustomView(APIView):
    def dispatch(self, request):
        return "custom"
    def post(self, request):
        return "unused"
urlpatterns = [path("generate/", CustomView.as_view())]
""",
    )
    assert not any(fact.provenance.rule_id.endswith("class_view_handler") for fact in facts)
