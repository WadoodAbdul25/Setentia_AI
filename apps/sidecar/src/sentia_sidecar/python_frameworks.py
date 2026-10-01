"""Conservative framework boundaries over Python source; repository code is never run.

Rules recognize imported framework APIs and unique lexical bindings. Registration
and declared workflow transitions are evidence of configuration, not observations
that a request executed those transitions. Unsupported dynamic patterns stay in
the ordinary AST index as unresolved calls.
"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sentia_sidecar.protocol import (
    FlowEntityKind,
    FlowProvenance,
    FlowRelationshipKind,
    FlowResolution,
    FlowSourceSpan,
)
from sentia_sidecar.python_relationships import _DefinitionMapper, _resolve_import_module
from sentia_sidecar.structural_index import CodeEntity, RelationshipFact, StructuralIndex

PYTHON_FRAMEWORK_EXTRACTOR = "sentia.python.frameworks"
PYTHON_FRAMEWORK_EXTRACTOR_VERSION = "1.0.0"
_FUNCTIONS = {FlowEntityKind.FUNCTION, FlowEntityKind.METHOD}
_STATE_GRAPHS = {"langgraph.graph.StateGraph", "langgraph.graph.state.StateGraph"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


@dataclass
class _Binding:
    value: ast.expr | CodeEntity | str | None


@dataclass
class _Scope:
    entity: CodeEntity
    node: ast.AST
    parent: _Scope | None
    bindings: dict[str, list[_Binding]] = field(default_factory=lambda: defaultdict(list))
    calls: list[tuple[ast.Call, bool]] = field(default_factory=list)
    returns: list[ast.Return] = field(default_factory=list)
    wildcard_import: bool = False


@dataclass
class _Graph:
    scope: _Scope
    constructor: ast.Call
    registrations: dict[str, list[tuple[CodeEntity, ast.Call]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    operations: list[tuple[ast.Call, bool]] = field(default_factory=list)
    entries: list[tuple[CodeEntity, ast.Call]] = field(default_factory=list)
    ambiguous_names: set[str] = field(default_factory=set)
    dynamic_registration: bool = False

    def target(self, name: str | None) -> tuple[CodeEntity, ast.Call] | None:
        if self.dynamic_registration or name in self.ambiguous_names:
            return None
        matches = self.registrations.get(name or "", [])
        return matches[0] if len(matches) == 1 else None


class _Collector(ast.NodeVisitor):
    def __init__(self, scope: _Scope, definitions: Mapping[int, CodeEntity]) -> None:
        self.scope = scope
        self.definitions = definitions
        self.conditional = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node)

    def _definition(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.bindings[node.name].append(
            _Binding(None if self.conditional else self.definitions.get(id(node)))
        )

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            value = alias.name if alias.asname else name
            self.scope.bindings[name].append(_Binding(value))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolve_import_module(
            current_module=self.scope.entity.module,
            path=self.scope.entity.path,
            imported_module=node.module,
            level=node.level,
        )
        for alias in node.names:
            if alias.name == "*":
                self.scope.wildcard_import = True
            if alias.name != "*":
                value = f"{module}.{alias.name}" if module else None
                self.scope.bindings[alias.asname or alias.name].append(_Binding(value))

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.scope.bindings[name].append(_Binding(None))

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.scope.bindings[name].append(_Binding(None))

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.scope.bindings[node.id].append(_Binding(None))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._assign(target, node.value)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assign(node.target, node.value)
        if node.value is not None:
            self.visit(node.value)

    def _assign(self, target: ast.expr, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            self.scope.bindings[target.id].append(_Binding(None if self.conditional else value))
        else:
            self.visit(target)

    def visit_Call(self, node: ast.Call) -> None:
        self.scope.calls.append((node, self.conditional))
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.scope.returns.append(node)
        self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        previous = self.conditional
        if isinstance(
            node,
            (
                ast.If,
                ast.IfExp,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.TryStar,
                ast.Match,
                ast.BoolOp,
                ast.comprehension,
            ),
        ):
            self.conditional = True
        super().generic_visit(node)
        self.conditional = previous


class _World:
    def __init__(self, index: StructuralIndex, sources: Mapping[str, str]) -> None:
        self.index = index
        self.scopes: dict[str, _Scope] = {}
        self.modules: dict[str, list[_Scope]] = defaultdict(list)
        self.graphs: dict[int, _Graph] = {}
        self.tasks: dict[str, FlowSourceSpan] = {}
        self.facts: list[RelationshipFact] = []
        children: dict[str, list[CodeEntity]] = defaultdict(list)
        for entity in index.entities:
            if entity.parent_entity_id:
                children[entity.parent_entity_id].append(entity)
        for siblings in children.values():
            siblings.sort(key=lambda entity: (entity.start_line, entity.start_column))
        for path, text in sorted(sources.items()):
            entities = index.get_file_entities(path)
            module = next((e for e in entities if e.kind == FlowEntityKind.MODULE), None)
            if module is None or hashlib.sha256(text.encode()).hexdigest() != module.content_hash:
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            mapper = _DefinitionMapper(module, children)
            mapper.visit(tree)
            module_scope = _Scope(module, tree, None)
            self.scopes[module.entity_id] = module_scope
            self.modules[module.module].append(module_scope)
            scopes = [module_scope]
            for node_id, entity in mapper.definitions.items():
                parent = self.scopes.get(entity.parent_entity_id or "")
                scope = _Scope(entity, mapper.nodes[node_id], parent)
                self.scopes[entity.entity_id] = scope
                scopes.append(scope)
            for scope in scopes:
                if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for argument in ast.walk(scope.node.args):
                        if isinstance(argument, ast.arg):
                            scope.bindings[argument.arg].append(_Binding(None))
                collector = _Collector(scope, mapper.definitions)
                for statement in getattr(scope.node, "body", []):
                    collector.visit(statement)

    def binding(self, scope: _Scope, name: str) -> tuple[_Scope, _Binding] | None:
        current: _Scope | None = scope
        while current is not None:
            if current.wildcard_import:
                return None
            matches = current.bindings.get(name)
            if matches:
                return (current, matches[0]) if len(matches) == 1 else None
            parent = current.parent
            if (
                current.entity.kind in _FUNCTIONS
                and parent is not None
                and parent.entity.kind == FlowEntityKind.CLASS
            ):
                parent = parent.parent
            current = parent
        return None

    def compiled_graph(
        self, scope: _Scope, expression: ast.expr, seen: frozenset[int] = frozenset()
    ) -> _Graph | None:
        origin = self.origin(scope, expression)
        if origin is None:
            return None
        owner, value = origin
        if id(value) in seen or not isinstance(value, ast.Call):
            return None
        seen = seen | {id(value)}
        if isinstance(value.func, ast.Attribute) and value.func.attr == "compile":
            return self.graph(owner, value.func.value)
        reference = self.reference(owner, value.func)
        if isinstance(reference, CodeEntity) and reference.kind in _FUNCTIONS:
            builder = self.scopes.get(reference.entity_id)
            if builder and len(builder.returns) == 1 and builder.returns[0].value is not None:
                return self.compiled_graph(builder, builder.returns[0].value, seen)
        return None

    def imported_binding(self, dotted: str) -> tuple[_Scope, _Binding] | None:
        module_name, _, name = dotted.rpartition(".")
        modules = self.modules.get(module_name, [])
        if len(modules) != 1:
            return None
        bindings = modules[0].bindings.get(name, [])
        return (modules[0], bindings[0]) if len(bindings) == 1 else None

    def reference(
        self, scope: _Scope, expression: ast.expr, seen: frozenset[int] = frozenset()
    ) -> CodeEntity | str | None:
        if isinstance(expression, ast.Name):
            binding = self.binding(scope, expression.id)
            return self._binding_reference(*binding, seen) if binding else None
        if isinstance(expression, ast.Attribute):
            receiver = self.reference(scope, expression.value, seen)
            if isinstance(receiver, str):
                dotted = f"{receiver}.{expression.attr}"
                binding = self.imported_binding(dotted)
                return self._binding_reference(*binding, seen) if binding else dotted
            if isinstance(receiver, CodeEntity) and receiver.kind == FlowEntityKind.CLASS:
                owner = self.scopes.get(receiver.entity_id)
                candidates = owner.bindings.get(expression.attr, []) if owner else []
                if owner and len(candidates) == 1:
                    return self._binding_reference(owner, candidates[0], seen)
        return None

    def _binding_reference(
        self, scope: _Scope, binding: _Binding, seen: frozenset[int]
    ) -> CodeEntity | str | None:
        if id(binding) in seen:
            return None
        seen = seen | {id(binding)}
        value = binding.value
        if isinstance(value, str):
            imported = self.imported_binding(value)
            return self._binding_reference(*imported, seen) if imported else value
        if isinstance(value, CodeEntity):
            return value
        if isinstance(value, ast.expr):
            return self.reference(scope, value, seen)
        return None

    def origin(
        self, scope: _Scope, expression: ast.expr, seen: frozenset[int] = frozenset()
    ) -> tuple[_Scope, ast.expr] | None:
        """Unwrap unique assignment/import aliases without inferring mutable values."""
        if not isinstance(expression, (ast.Name, ast.Attribute)):
            return scope, expression
        if isinstance(expression, ast.Name):
            binding = self.binding(scope, expression.id)
        else:
            ref = self.reference(scope, expression)
            binding = self.imported_binding(ref) if isinstance(ref, str) else None
        while binding is not None:
            owner, item = binding
            if id(item) in seen:
                return None
            seen = seen | {id(item)}
            if isinstance(item.value, ast.expr):
                return self.origin(owner, item.value, seen)
            if isinstance(item.value, str):
                binding = self.imported_binding(item.value)
            else:
                return None
        return None

    def constructor(self, scope: _Scope, expression: ast.expr) -> CodeEntity | str | None:
        origin = self.origin(scope, expression)
        if origin and isinstance(origin[1], ast.Call):
            return self.reference(origin[0], origin[1].func)
        return None

    def span(self, scope: _Scope, node: ast.expr | ast.stmt) -> FlowSourceSpan:
        return FlowSourceSpan(
            path=scope.entity.path,
            start_line=node.lineno,
            start_column=node.col_offset,
            end_line=node.end_lineno or node.lineno,
            end_column=node.end_col_offset or node.col_offset,
            content_hash=scope.entity.content_hash,
            repository_revision=self.index.repository_revision,
        )

    def emit(
        self,
        source: CodeEntity,
        target: CodeEntity,
        kind: FlowRelationshipKind,
        *,
        rule: str,
        evidence: list[FlowSourceSpan],
        conditional: bool = False,
        asynchronous: bool = False,
        **metadata: Any,
    ) -> None:
        evidence.append(target.flow_source_span())
        self.facts.append(
            RelationshipFact.create(
                repository_revision=self.index.repository_revision,
                source_entity_id=source.entity_id,
                target_entity_id=target.entity_id,
                kind=kind,
                resolution=FlowResolution.FRAMEWORK_INFERRED,
                evidence=evidence,
                provenance=FlowProvenance(
                    extractor=PYTHON_FRAMEWORK_EXTRACTOR,
                    extractor_version=PYTHON_FRAMEWORK_EXTRACTOR_VERSION,
                    rule_id=f"python.framework.{rule}",
                ),
                conditional=conditional,
                asynchronous=asynchronous,
                metadata=metadata,
            )
        )

    def graph(
        self, scope: _Scope, expression: ast.expr, seen: frozenset[int] = frozenset()
    ) -> _Graph | None:
        origin = self.origin(scope, expression)
        if origin is None:
            return None
        owner, value = origin
        if id(value) in seen or not isinstance(value, ast.Call):
            return None
        seen = seen | {id(value)}
        reference = self.reference(owner, value.func)
        if isinstance(reference, str) and reference in _STATE_GRAPHS:
            if id(value) not in self.graphs:
                self.graphs[id(value)] = _Graph(owner, value)
            return self.graphs[id(value)]
        if isinstance(value.func, ast.Attribute) and value.func.attr == "compile":
            return self.graph(owner, value.func.value, seen)
        if isinstance(reference, CodeEntity) and reference.kind in _FUNCTIONS:
            builder = self.scopes.get(reference.entity_id)
            if builder and len(builder.returns) == 1 and builder.returns[0].value is not None:
                return self.graph(builder, builder.returns[0].value, seen)
        return None


def _argument(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((item.value for item in call.keywords if item.arg == keyword), None)


def _literal(expression: ast.expr | None) -> str | None:
    return (
        expression.value
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str)
        else None
    )


def _task_decorator(world: _World, scope: _Scope, decorator: ast.expr) -> bool:
    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    reference = world.reference(scope, expression)
    if isinstance(reference, str) and reference in {"celery.shared_task", "celery.task"}:
        return True
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "task"
        and world.constructor(scope, expression.value) == "celery.Celery"
    )


def _extract_tasks(world: _World) -> None:
    for scope in world.scopes.values():
        if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)) and scope.parent:
            for decorator in scope.node.decorator_list:
                if _task_decorator(world, scope.parent, decorator):
                    world.tasks[scope.entity.entity_id] = world.span(scope.parent, decorator)
    for scope in world.scopes.values():
        for call, conditional in scope.calls:
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
                "delay",
                "apply_async",
            }:
                continue
            target = world.reference(scope, call.func.value)
            if isinstance(target, CodeEntity) and target.entity_id in world.tasks:
                world.emit(
                    scope.entity,
                    target,
                    FlowRelationshipKind.PUBLISHES_EVENT,
                    rule="celery.dispatch",
                    evidence=[world.span(scope, call), world.tasks[target.entity_id]],
                    conditional=conditional,
                    asynchronous=True,
                    framework="celery",
                    semantic="task_dispatch",
                    method=call.func.attr,
                    execution="dispatch_declared",
                )


def _extract_graphs(world: _World) -> None:
    # Collect all registrations before resolving transitions; source order is not
    # a runtime claim, and duplicate node names deliberately remain ambiguous.
    for scope in world.scopes.values():
        for call, conditional in scope.calls:
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in {
                "add_node",
                "add_edge",
                "add_conditional_edges",
                "set_entry_point",
            }:
                continue
            graph = world.graph(scope, call.func.value)
            if graph is None or graph.scope is not scope:
                continue
            graph.operations.append((call, conditional))
            if call.func.attr != "add_node":
                continue
            node = _argument(call, 0, "node")
            action = _argument(call, 1, "action")
            if node is not None and action is None and not isinstance(node, ast.Constant):
                action = node
            target = world.reference(scope, action) if action is not None else None
            name = _literal(node)
            if name is None and isinstance(target, CodeEntity) and action is node:
                name = target.name
            if name is None:
                graph.dynamic_registration = True
                continue
            if conditional or not isinstance(target, CodeEntity) or target.kind not in _FUNCTIONS:
                graph.ambiguous_names.add(name)
                continue
            if isinstance(target, CodeEntity) and target.kind in _FUNCTIONS:
                graph.registrations[name].append((target, call))
    for graph in list(world.graphs.values()):
        for name, registrations in graph.registrations.items():
            if graph.target(name) is None:
                continue
            target, call = registrations[0]
            world.emit(
                graph.scope.entity,
                target,
                FlowRelationshipKind.REGISTERS_HANDLER,
                rule="langgraph.node",
                evidence=[world.span(graph.scope, call)],
                framework="langgraph",
                semantic="workflow_registration",
                graphEntityId=graph.scope.entity.entity_id,
                node=name,
                execution="registration_only",
            )
        for call, conditional in graph.operations:
            _graph_transition(world, graph, call, conditional)
    for scope in world.scopes.values():
        for call, conditional in scope.calls:
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
                "invoke",
                "ainvoke",
                "stream",
                "astream",
            }:
                continue
            graph = world.compiled_graph(scope, call.func.value)
            if graph is None:
                continue
            for target, entry in graph.entries:
                asynchronous = call.func.attr in {"ainvoke", "astream"}
                world.emit(
                    scope.entity,
                    target,
                    FlowRelationshipKind.AWAITS if asynchronous else FlowRelationshipKind.CALLS,
                    rule="langgraph.invoke",
                    evidence=[world.span(scope, call), world.span(graph.scope, entry)],
                    conditional=conditional,
                    asynchronous=asynchronous,
                    framework="langgraph",
                    semantic="workflow_invocation",
                    graphEntityId=graph.scope.entity.entity_id,
                    execution="invocation_declared",
                    method=call.func.attr,
                )


def _graph_transition(world: _World, graph: _Graph, call: ast.Call, conditional: bool) -> None:
    assert isinstance(call.func, ast.Attribute)
    method = call.func.attr
    if method == "set_entry_point":
        target = graph.target(_literal(_argument(call, 0, "key")))
        if target and not conditional:
            graph.entries.append((target[0], call))
        return
    if method not in {"add_edge", "add_conditional_edges"}:
        return
    start = _argument(call, 0, "start_key" if method == "add_edge" else "source")
    start_ref = world.reference(graph.scope, start) if start is not None else None
    is_entry = (
        start_ref in {"langgraph.graph.START", "langgraph.graph.state.START"}
        if (isinstance(start_ref, str))
        else False
    )
    source = graph.target(_literal(start))
    if method == "add_edge":
        target = graph.target(_literal(_argument(call, 1, "end_key")))
        if is_entry and target and not conditional:
            graph.entries.append((target[0], call))
        if source and target:
            _emit_transition(world, graph, source, target, call, conditional)
        return
    route = _argument(call, 1, "path")
    router = world.reference(graph.scope, route) if route is not None else None
    if not source or not isinstance(router, CodeEntity) or router.kind not in _FUNCTIONS:
        return
    router_pair = (router, call)
    _emit_transition(world, graph, source, router_pair, call, True, semantic="workflow_routing")
    path_map = _argument(call, 2, "path_map")
    destinations: list[ast.expr] = []
    if isinstance(path_map, ast.Dict):
        destinations = path_map.values
    elif isinstance(path_map, (ast.List, ast.Tuple)):
        destinations = path_map.elts
    for destination in destinations:
        target = graph.target(_literal(destination))
        if target:
            _emit_transition(world, graph, router_pair, target, call, True)


def _emit_transition(
    world: _World,
    graph: _Graph,
    source: tuple[CodeEntity, ast.Call],
    target: tuple[CodeEntity, ast.Call],
    call: ast.Call,
    conditional: bool,
    semantic: str = "workflow_transition",
) -> None:
    world.emit(
        source[0],
        target[0],
        FlowRelationshipKind.CALLS,
        rule=f"langgraph.{semantic}",
        evidence=[
            world.span(graph.scope, call),
            world.span(graph.scope, source[1]),
            world.span(graph.scope, target[1]),
        ],
        conditional=conditional,
        framework="langgraph",
        semantic=semantic,
        execution="declared_transition",
        graphEntityId=graph.scope.entity.entity_id,
    )


def _extract_http(world: _World) -> None:
    for scope in world.scopes.values():
        node = scope.node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and scope.parent:
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                if not isinstance(function, ast.Attribute) or function.attr not in _HTTP_METHODS:
                    continue
                constructor = world.constructor(scope.parent, function.value)
                if constructor not in {"fastapi.FastAPI", "fastapi.APIRouter"}:
                    continue
                path = _literal(_argument(decorator, 0, "path"))
                if path is None:
                    continue
                origin = world.origin(scope.parent, function.value)
                prefix: str | None = ""
                if origin and isinstance(origin[1], ast.Call):
                    prefix_node = _argument(origin[1], 100, "prefix")
                    prefix = _literal(prefix_node) if prefix_node is not None else ""
                world.emit(
                    scope.parent.entity,
                    scope.entity,
                    FlowRelationshipKind.HANDLES_HTTP_REQUEST,
                    rule="fastapi.route",
                    evidence=[world.span(scope.parent, decorator)],
                    framework="fastapi",
                    semantic="http_handler",
                    method=function.attr.upper(),
                    route=path,
                    routerPrefix=prefix,
                    execution="registration_only",
                    effectivePath=None,
                    mountStatus="not_resolved",
                )
        for call, conditional in scope.calls:
            reference = world.reference(scope, call.func)
            if reference not in {"django.urls.path", "django.urls.re_path"}:
                continue
            path = _literal(_argument(call, 0, "route"))
            view = _argument(call, 1, "view")
            if path is None or view is None:
                continue
            target = world.reference(scope, view)
            if (
                isinstance(view, ast.Call)
                and isinstance(view.func, ast.Attribute)
                and view.func.attr == "as_view"
            ):
                target = world.reference(scope, view.func.value)
            if isinstance(target, CodeEntity):
                world.emit(
                    scope.entity,
                    target,
                    FlowRelationshipKind.HANDLES_HTTP_REQUEST,
                    rule="django.route",
                    evidence=[world.span(scope, call)],
                    conditional=conditional,
                    framework="django",
                    semantic="http_handler",
                    route=path,
                    routeSyntax="regex" if str(reference).endswith("re_path") else "path",
                    execution="registration_only",
                    effectivePath=None,
                    mountStatus="not_resolved",
                )
                view_scope = world.scopes.get(target.entity_id)
                if (
                    view_scope is None
                    or not isinstance(view_scope.node, ast.ClassDef)
                    or view_scope.parent is None
                    or any(name in view_scope.bindings for name in ("as_view", "dispatch"))
                ):
                    continue
                known_view = any(
                    world.reference(view_scope.parent, base)
                    in {
                        "rest_framework.views.APIView",
                        "django.views.View",
                        "django.views.generic.View",
                    }
                    for base in view_scope.node.bases
                )
                if not known_view:
                    continue
                for method in sorted(_HTTP_METHODS):
                    bindings = view_scope.bindings.get(method, [])
                    if len(bindings) != 1 or not isinstance(bindings[0].value, CodeEntity):
                        continue
                    handler = bindings[0].value
                    if handler.kind != FlowEntityKind.METHOD:
                        continue
                    world.emit(
                        scope.entity,
                        handler,
                        FlowRelationshipKind.HANDLES_HTTP_REQUEST,
                        rule="django.class_view_handler",
                        evidence=[world.span(scope, call), target.flow_source_span()],
                        conditional=conditional,
                        framework="django",
                        semantic="http_handler",
                        route=path,
                        method=method.upper(),
                        execution="registration_only",
                        effectivePath=None,
                        mountStatus="not_resolved",
                    )


def extract_python_framework_relationships(
    index: StructuralIndex, sources: Mapping[str, str]
) -> tuple[RelationshipFact, ...]:
    """Extract import-backed Celery, LangGraph, FastAPI and Django relationships.

    Existing calls remain intact. Consumers must use semantic/execution metadata
    to distinguish registrations and possible transitions from invocation sites.
    Framework versions, dynamic route mounts and task names are not guessed.
    """
    world = _World(index, sources)
    _extract_tasks(world)
    _extract_graphs(world)
    _extract_http(world)
    return tuple(
        sorted(
            {fact.relationship_id: fact for fact in world.facts}.values(),
            key=lambda fact: fact.relationship_id,
        )
    )
