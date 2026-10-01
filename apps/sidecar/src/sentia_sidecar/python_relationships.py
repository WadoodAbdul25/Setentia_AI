from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sentia_sidecar.protocol import (
    FlowEntityKind,
    FlowProvenance,
    FlowRelationshipKind,
    FlowResolution,
    FlowSourceSpan,
)
from sentia_sidecar.structural_index import CodeEntity, RelationshipFact, StructuralIndex

PYTHON_RELATIONSHIP_EXTRACTOR = "sentia.python.ast"
PYTHON_RELATIONSHIP_EXTRACTOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class _ResolvedTarget:
    entity: CodeEntity
    rule: str


@dataclass
class _ScopeBindings:
    local_names: set[str] = field(default_factory=set)
    global_names: set[str] = field(default_factory=set)
    nonlocal_names: set[str] = field(default_factory=set)
    module_aliases: dict[str, str] = field(default_factory=dict)
    symbol_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    relative_import_names: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _ParsedModule:
    path: str
    text: str
    tree: ast.Module
    module_entity: CodeEntity
    file_entity: CodeEntity
    definitions: Mapping[int, CodeEntity]
    scope_nodes: Mapping[str, ast.AST]
    bindings: Mapping[str, _ScopeBindings]
    receiver_names: Mapping[str, frozenset[str]]


def extract_python_relationships(
    index: StructuralIndex,
    sources: Mapping[str, str],
) -> tuple[RelationshipFact, ...]:
    """Extract conservative Python relationships without importing repository code."""

    modules_by_name = {
        entity.module: entity for entity in index.entities if entity.kind == FlowEntityKind.MODULE
    }
    children_by_parent: dict[str, list[CodeEntity]] = defaultdict(list)
    for entity in index.entities:
        if entity.parent_entity_id is not None:
            children_by_parent[entity.parent_entity_id].append(entity)
    for children in children_by_parent.values():
        children.sort(
            key=lambda entity: (
                entity.start_line,
                entity.start_column,
                entity.end_line,
                entity.end_column,
                entity.entity_id,
            )
        )

    parsed_modules: list[_ParsedModule] = []
    for path, text in sorted(sources.items()):
        file_entities = index.get_file_entities(path)
        module_entity = next(
            (entity for entity in file_entities if entity.kind == FlowEntityKind.MODULE),
            None,
        )
        file_entity = next(
            (entity for entity in file_entities if entity.kind == FlowEntityKind.FILE),
            None,
        )
        if module_entity is None or file_entity is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        mapper = _DefinitionMapper(module_entity, children_by_parent)
        mapper.visit(tree)
        scope_nodes: dict[str, ast.AST] = {module_entity.entity_id: tree}
        scope_nodes.update(
            {
                entity.entity_id: node
                for node_id, entity in mapper.definitions.items()
                if (node := mapper.nodes.get(node_id)) is not None
            }
        )
        bindings: dict[str, _ScopeBindings] = {}
        receiver_names: dict[str, frozenset[str]] = {}
        for entity_id, scope_node in scope_nodes.items():
            scope_entity = index.get_entity(entity_id)
            if scope_entity is None:
                continue
            bindings[entity_id] = _collect_scope_bindings(
                scope_node,
                current_module=module_entity.module,
                path=path,
            )
            receiver_names[entity_id] = _method_receiver_names(scope_entity, scope_node)
        parsed_modules.append(
            _ParsedModule(
                path=path,
                text=text,
                tree=tree,
                module_entity=module_entity,
                file_entity=file_entity,
                definitions=mapper.definitions,
                scope_nodes=scope_nodes,
                bindings=bindings,
                receiver_names=receiver_names,
            )
        )

    facts: list[RelationshipFact] = []
    for parsed in parsed_modules:
        extractor = _PythonRelationshipVisitor(
            index=index,
            parsed=parsed,
            modules_by_name=modules_by_name,
            children_by_parent=children_by_parent,
        )
        extractor.visit(parsed.tree)
        facts.extend(extractor.facts)
    facts.sort(key=lambda fact: fact.relationship_id)
    return tuple(facts)


class _DefinitionMapper(ast.NodeVisitor):
    def __init__(
        self,
        module_entity: CodeEntity,
        children_by_parent: Mapping[str, Sequence[CodeEntity]],
    ) -> None:
        self._scopes = [module_entity]
        self._children_by_parent = children_by_parent
        self._occurrences: dict[tuple[str, str], int] = defaultdict(int)
        self.definitions: dict[int, CodeEntity] = {}
        self.nodes: dict[int, ast.AST] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node)

    def _visit_definition(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        parent = self._scopes[-1]
        key = (parent.entity_id, node.name)
        candidates = [
            entity
            for entity in self._children_by_parent.get(parent.entity_id, ())
            if entity.name == node.name
        ]
        occurrence = self._occurrences[key]
        self._occurrences[key] += 1
        if occurrence >= len(candidates):
            return
        entity = candidates[occurrence]
        self.definitions[id(node)] = entity
        self.nodes[id(node)] = node
        self._scopes.append(entity)
        for statement in node.body:
            self.visit(statement)
        self._scopes.pop()


class _BindingCollector(ast.NodeVisitor):
    def __init__(self, *, current_module: str, path: str) -> None:
        self.current_module = current_module
        self.path = path
        self.bindings = _ScopeBindings()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bindings.local_names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.bindings.local_names.add(node.arg)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.bindings.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.bindings.nonlocal_names.update(node.names)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.partition(".")[0]
            self.bindings.module_aliases[local_name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolve_import_module(
            current_module=self.current_module,
            path=self.path,
            imported_module=node.module,
            level=node.level,
        )
        if module is None:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self.bindings.symbol_aliases[local_name] = (module, alias.name)
            if node.level > 0:
                self.bindings.relative_import_names.add(local_name)


def _collect_scope_bindings(
    scope_node: ast.AST,
    *,
    current_module: str,
    path: str,
) -> _ScopeBindings:
    collector = _BindingCollector(current_module=current_module, path=path)
    if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        collector.visit(scope_node.args)
    body = getattr(scope_node, "body", ())
    if isinstance(body, list):
        for statement in body:
            collector.visit(statement)
    collector.bindings.local_names.difference_update(collector.bindings.global_names)
    collector.bindings.local_names.difference_update(collector.bindings.nonlocal_names)
    return collector.bindings


def _method_receiver_names(entity: CodeEntity, scope_node: ast.AST) -> frozenset[str]:
    if entity.kind != FlowEntityKind.METHOD or not isinstance(
        scope_node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        return frozenset()
    if any(_decorator_name(decorator) == "staticmethod" for decorator in scope_node.decorator_list):
        return frozenset()
    positional = [*scope_node.args.posonlyargs, *scope_node.args.args]
    if not positional:
        return frozenset()
    return frozenset({positional[0].arg})


class _PythonRelationshipVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        index: StructuralIndex,
        parsed: _ParsedModule,
        modules_by_name: Mapping[str, CodeEntity],
        children_by_parent: Mapping[str, Sequence[CodeEntity]],
    ) -> None:
        self.index = index
        self.parsed = parsed
        self.modules_by_name = modules_by_name
        self.children_by_parent = children_by_parent
        self.facts: list[RelationshipFact] = []
        self._scopes = [parsed.module_entity]
        self._conditional_depth = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._visit_definition_body(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        self._visit_call(node, awaited=False)

    def visit_Await(self, node: ast.Await) -> None:
        if isinstance(node.value, ast.Call):
            self._visit_call(node.value, awaited=True)
        else:
            self.visit(node.value)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_conditionally(node.body)
        self._visit_conditionally(node.orelse)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._visit_conditionally((node.body, node.orelse))

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_conditionally(node.body)
        self._visit_conditionally(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_conditionally(node.body)
        self._visit_conditionally(node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        self._visit_conditionally((*node.body, *node.handlers, *node.orelse, *node.finalbody))

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        self._visit_conditionally(node.cases)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not node.values:
            return
        self.visit(node.values[0])
        self._visit_conditionally(node.values[1:])

    def _visit_function_definition(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults:
            self.visit(default)
        for keyword_default in node.args.kw_defaults:
            if keyword_default is not None:
                self.visit(keyword_default)
        self._visit_definition_body(node)

    def _visit_definition_body(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        entity = self.parsed.definitions.get(id(node))
        if entity is None:
            return
        self._scopes.append(entity)
        for statement in node.body:
            self.visit(statement)
        self._scopes.pop()

    def _visit_call(self, node: ast.Call, *, awaited: bool) -> None:
        source = self._scopes[-1]
        resolved = self._resolve_callee(node.func)
        target = resolved.entity if resolved is not None else None
        if awaited:
            kind = FlowRelationshipKind.AWAITS
            rule_prefix = "python.await"
        elif target is not None and target.kind == FlowEntityKind.CLASS:
            kind = FlowRelationshipKind.CONSTRUCTS
            rule_prefix = "python.construct"
        else:
            kind = FlowRelationshipKind.CALLS
            rule_prefix = "python.call"
        callee = _callee_key(node.func)
        target_import = self._target_import(node.func) if target is None else None
        rule_suffix = resolved.rule if resolved is not None else "dynamic"
        evidence = self._source_span(node)
        self.facts.append(
            RelationshipFact.create(
                repository_revision=self.index.repository_revision,
                source_entity_id=source.entity_id,
                target_entity_id=target.entity_id if target is not None else None,
                unresolved_key=None if target is not None else callee,
                kind=kind,
                resolution=(
                    FlowResolution.STATICALLY_RESOLVED
                    if target is not None
                    else FlowResolution.UNRESOLVED
                ),
                evidence=(evidence,),
                provenance=FlowProvenance(
                    extractor=PYTHON_RELATIONSHIP_EXTRACTOR,
                    extractor_version=PYTHON_RELATIONSHIP_EXTRACTOR_VERSION,
                    rule_id=f"{rule_prefix}.{rule_suffix}",
                ),
                conditional=self._conditional_depth > 0,
                asynchronous=awaited,
                metadata={
                    "callee": callee,
                    **({"targetImport": target_import} if target_import is not None else {}),
                },
            )
        )
        self.generic_visit(node)

    def _resolve_callee(self, callee: ast.expr) -> _ResolvedTarget | None:
        if isinstance(callee, ast.Name):
            return self._resolve_name(callee.id)
        if not isinstance(callee, ast.Attribute):
            return None
        if isinstance(callee.value, ast.Name):
            receiver = callee.value.id
            containing_class = self._receiver_class(receiver)
            if containing_class is not None:
                target = self._unique_child(containing_class.entity_id, callee.attr)
                if target is not None and target.kind == FlowEntityKind.METHOD:
                    return _ResolvedTarget(target, "self_method")
            resolved_receiver = self._resolve_name(receiver)
            if (
                resolved_receiver is not None
                and resolved_receiver.entity.kind == FlowEntityKind.CLASS
            ):
                target = self._unique_child(resolved_receiver.entity.entity_id, callee.attr)
                if target is not None and target.kind == FlowEntityKind.METHOD:
                    return _ResolvedTarget(target, "class_method")
            module_name = self._module_alias(receiver)
            if module_name is not None:
                target = self._module_symbol(module_name, callee.attr)
                if target is not None:
                    return _ResolvedTarget(target, "module_attribute")
        return None

    def _resolve_name(self, name: str) -> _ResolvedTarget | None:
        module_scope = self.parsed.module_entity
        use_module_scope = False
        for scope in self._resolution_scopes():
            if use_module_scope and scope.entity_id != module_scope.entity_id:
                continue
            bindings = self.parsed.bindings.get(scope.entity_id, _ScopeBindings())
            if name in bindings.global_names and scope.entity_id != module_scope.entity_id:
                use_module_scope = True
                continue
            if name in bindings.nonlocal_names:
                continue
            declarations = [
                entity
                for entity in self.children_by_parent.get(scope.entity_id, ())
                if entity.name == name
                and entity.kind
                in {
                    FlowEntityKind.CLASS,
                    FlowEntityKind.FUNCTION,
                    FlowEntityKind.METHOD,
                }
            ]
            has_other_binding = (
                name in bindings.local_names
                or name in bindings.symbol_aliases
                or name in bindings.module_aliases
            )
            if declarations and has_other_binding:
                return None
            if len(declarations) == 1:
                return _ResolvedTarget(declarations[0], "lexical_name")
            if len(declarations) > 1:
                return None
            imported_symbol = bindings.symbol_aliases.get(name)
            if imported_symbol is not None:
                if name in bindings.local_names or name in bindings.module_aliases:
                    return None
                target = self._module_symbol(*imported_symbol)
                return _ResolvedTarget(target, "imported_symbol") if target is not None else None
            if name in bindings.module_aliases or name in bindings.local_names:
                return None
        return None

    def _module_alias(self, name: str) -> str | None:
        module_scope = self.parsed.module_entity
        use_module_scope = False
        for scope in self._resolution_scopes():
            if use_module_scope and scope.entity_id != module_scope.entity_id:
                continue
            bindings = self.parsed.bindings.get(scope.entity_id, _ScopeBindings())
            if name in bindings.global_names and scope.entity_id != module_scope.entity_id:
                use_module_scope = True
                continue
            if name in bindings.nonlocal_names:
                continue
            module = bindings.module_aliases.get(name)
            if module is not None:
                if (
                    name in bindings.local_names
                    or name in bindings.symbol_aliases
                    or self._unique_child(scope.entity_id, name) is not None
                ):
                    return None
                return module
            if (
                name in bindings.symbol_aliases
                or name in bindings.local_names
                or self._unique_child(scope.entity_id, name) is not None
            ):
                return None
        return None

    def _module_symbol(self, module_name: str, symbol: str) -> CodeEntity | None:
        module = self.modules_by_name.get(module_name)
        if module is None:
            return None
        return self._unique_child(module.entity_id, symbol)

    def _target_import(self, callee: ast.expr) -> dict[str, Any] | None:
        root_name = _callee_root_name(callee)
        if root_name is None:
            return None
        module_scope = self.parsed.module_entity
        use_module_scope = False
        for scope in self._resolution_scopes():
            if use_module_scope and scope.entity_id != module_scope.entity_id:
                continue
            bindings = self.parsed.bindings.get(scope.entity_id, _ScopeBindings())
            if root_name in bindings.global_names and scope.entity_id != module_scope.entity_id:
                use_module_scope = True
                continue
            if root_name in bindings.nonlocal_names:
                continue
            declaration = self._unique_child(scope.entity_id, root_name)
            if root_name in bindings.local_names or declaration is not None:
                return None
            imported_symbol = bindings.symbol_aliases.get(root_name)
            if imported_symbol is not None:
                module, symbol = imported_symbol
                return {
                    "module": module,
                    "symbol": symbol,
                    "relative": root_name in bindings.relative_import_names,
                }
            imported_module = bindings.module_aliases.get(root_name)
            if imported_module is not None:
                return {"module": imported_module, "relative": False}
        return None

    def _unique_child(self, parent_entity_id: str, name: str) -> CodeEntity | None:
        candidates = [
            entity
            for entity in self.children_by_parent.get(parent_entity_id, ())
            if entity.name == name
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _receiver_class(self, receiver_name: str) -> CodeEntity | None:
        current: CodeEntity | None = self._scopes[-1]
        while current is not None:
            if receiver_name in self.parsed.receiver_names.get(current.entity_id, frozenset()):
                parent = (
                    self.index.get_entity(current.parent_entity_id)
                    if current.parent_entity_id is not None
                    else None
                )
                return (
                    parent if parent is not None and parent.kind == FlowEntityKind.CLASS else None
                )
            current = (
                self.index.get_entity(current.parent_entity_id)
                if current.parent_entity_id is not None
                else None
            )
        return None

    def _resolution_scopes(self) -> tuple[CodeEntity, ...]:
        scopes: list[CodeEntity] = []
        current: CodeEntity | None = self._scopes[-1]
        while current is not None:
            if current.kind != FlowEntityKind.FILE:
                scopes.append(current)
            parent = (
                self.index.get_entity(current.parent_entity_id)
                if current.parent_entity_id is not None
                else None
            )
            if (
                current.kind in {FlowEntityKind.FUNCTION, FlowEntityKind.METHOD}
                and parent is not None
                and parent.kind == FlowEntityKind.CLASS
            ):
                parent = (
                    self.index.get_entity(parent.parent_entity_id)
                    if parent.parent_entity_id is not None
                    else None
                )
            current = parent
        return tuple(scopes)

    def _source_span(self, node: ast.Call) -> FlowSourceSpan:
        return FlowSourceSpan(
            path=self.parsed.path,
            start_line=node.lineno,
            start_column=node.col_offset,
            end_line=node.end_lineno or node.lineno,
            end_column=node.end_col_offset or node.col_offset,
            content_hash=self.parsed.file_entity.content_hash,
            repository_revision=self.index.repository_revision,
        )

    def _visit_conditionally(self, nodes: Sequence[ast.AST]) -> None:
        self._conditional_depth += 1
        try:
            for node in nodes:
                self.visit(node)
        finally:
            self._conditional_depth -= 1


def _resolve_import_module(
    *,
    current_module: str,
    path: str,
    imported_module: str | None,
    level: int,
) -> str | None:
    if level == 0:
        return imported_module
    module_parts = current_module.split(".")
    package_parts = module_parts if path.endswith("/__init__.py") else module_parts[:-1]
    ascents = level - 1
    if ascents > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - ascents]
    if imported_module:
        base.extend(imported_module.split("."))
    return ".".join(base) or None


def _decorator_name(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return None


def _callee_key(callee: ast.expr) -> str:
    try:
        value = ast.unparse(callee)
    except (TypeError, ValueError):
        value = type(callee).__name__
    normalized = " ".join(value.split())
    return (normalized or type(callee).__name__)[:1_000]


def _callee_root_name(callee: ast.expr) -> str | None:
    current = callee
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None
