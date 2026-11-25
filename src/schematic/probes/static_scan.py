"""Scan a package with the target's own mypy and write what it resolves as JSON.

Runs inside the target's interpreter, so it imports only the standard library and
mypy: `python -I -B static_scan.py <package dir> <output file>`, from the target's root.
"""

import ast
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from mypy import build
from mypy.argmap import map_actuals_to_formals
from mypy.main import process_options
from mypy.nodes import (
    ARG_NAMED,
    ARG_NAMED_OPT,
    ARG_STAR,
    ARG_STAR2,
    AssignmentStmt,
    CallExpr,
    ClassDef,
    Decorator,
    Expression,
    FuncBase,
    FuncDef,
    ListExpr,
    MemberExpr,
    MypyFile,
    NameExpr,
    OverloadedFuncDef,
    RaiseStmt,
    RefExpr,
    ReturnStmt,
    StrExpr,
    SuperExpr,
    SymbolNode,
    TypeInfo,
    Var,
)
from mypy.traverser import TraverserVisitor
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    Overloaded,
    ProperType,
    TupleType,
    Type,
    TypeOfAny,
    TypeType,
    UnionType,
    get_proper_type,
)

type Json = dict[str, object]

FUNCTION_NODES = (FuncDef, Decorator, OverloadedFuncDef)


def type_refs(root: Type | None) -> list[str]:
    found: list[str] = []
    pending: list[Type | None] = [root]
    while pending:
        proper = get_proper_type(pending.pop())
        if isinstance(proper, Instance):
            found.append(proper.type.fullname)
            pending.extend(proper.args)
        elif isinstance(proper, (UnionType, TupleType)):
            pending.extend(proper.items)
        elif isinstance(proper, CallableType):
            pending.extend(proper.arg_types)
            pending.append(proper.ret_type)
        elif isinstance(proper, TypeType):
            pending.append(proper.item)
    return list(dict.fromkeys(found))


def type_json(root: Type | None) -> Json:
    return {"text": "" if root is None else str(root), "refs": type_refs(root)}


def param_kind(kind: object) -> str:
    if kind in (ARG_NAMED, ARG_NAMED_OPT):
        return "keyword"
    if kind == ARG_STAR:
        return "var-positional"
    if kind == ARG_STAR2:
        return "var-keyword"
    return "positional"


def signature_params(signature: CallableType, *, bound: bool) -> list[Json]:
    rows: list[Json] = [
        {"name": name or "", "kind": param_kind(kind), "type": type_json(arg_type)}
        for name, kind, arg_type in zip(signature.arg_names, signature.arg_kinds, signature.arg_types, strict=True)
    ]
    return rows[1:] if bound and rows else rows


def function_of(node: SymbolNode | None) -> FuncDef | None:
    if isinstance(node, Decorator):
        return node.func
    if isinstance(node, OverloadedFuncDef):
        first = node.items[0] if node.items else None
        return first.func if isinstance(first, Decorator) else first
    return node if isinstance(node, FuncDef) else None


def is_method(function: FuncDef) -> bool:
    return bool(function.info) and not function.is_static


def owner_is_protocol(function: FuncBase) -> bool:
    return bool(function.info) and function.info.is_protocol


def literal(expression: Expression) -> str | None:
    return expression.value if isinstance(expression, StrExpr) else None


def template(expression: Expression) -> str | None:
    """Read a string, or an f-string with each name it formats in as `⟨name⟩`; None for anything else.

    mypy has turned an f-string into `"".join([...])` over `"{:{}}".format(value, spec)` calls.
    """
    if isinstance(expression, StrExpr):
        return expression.value
    if isinstance(expression, NameExpr):
        constant = expression.node.final_value if isinstance(expression.node, Var) else None
        return constant if isinstance(constant, str) else f"⟨{expression.name}⟩"
    if not (
        isinstance(expression, CallExpr)
        and isinstance(expression.callee, MemberExpr)
        and isinstance(expression.callee.expr, StrExpr)
        and expression.args
    ):
        return None
    method, first = expression.callee.name, expression.args[0]
    if method == "join" and isinstance(first, ListExpr):
        parts = [template(item) for item in first.items]
        return "".join(part for part in parts if part is not None) if None not in parts else None
    if method == "format" and isinstance(first, NameExpr):
        return template(first)
    return None


def module_variable(expression: Expression) -> str | None:
    if isinstance(expression, RefExpr) and isinstance(expression.node, Var) and "." in expression.node.fullname:
        return expression.node.fullname
    return None


def literal_keywords(call: CallExpr) -> dict[str, str]:
    return {
        name: value
        for name, argument in zip(call.arg_names, call.args, strict=True)
        if name is not None and (value := literal(argument)) is not None
    }


def expression_path(expression: Expression) -> str:
    if isinstance(expression, NameExpr):
        return expression.name
    if isinstance(expression, MemberExpr):
        return f"{expression_path(expression.expr)}.{expression.name}"
    if isinstance(expression, CallExpr):
        return f"{expression_path(expression.callee)}()"
    return "…"


class Resolver:
    def __init__(self, *, types: dict[Expression, Type]) -> None:
        self._types = types
        self._names: dict[FuncDef, str] = {}

    def qualify(self, function: FuncDef, *, enclosing: FuncDef | None) -> str:
        """Name a function as Python's `__qualname__` does, which mypy does not for nested functions."""
        name = f"{self.name_of(enclosing)}.<locals>.{function.name}" if enclosing else function.fullname
        self._names[function] = name
        return name

    def name_of(self, node: SymbolNode) -> str:
        function = function_of(node)
        return self._names.get(function, node.fullname) if function is not None else node.fullname

    def type_of(self, expression: Expression) -> ProperType:
        found = self._types.get(expression)
        return get_proper_type(found) if found is not None else AnyType(TypeOfAny.special_form)

    def signature(self, call: CallExpr) -> CallableType | None:
        callee = self.type_of(call.callee)
        if isinstance(callee, Overloaded):
            return callee.items[0]
        return callee if isinstance(callee, CallableType) else None

    def target(self, call: CallExpr) -> tuple[str, str]:
        callee = call.callee
        if isinstance(callee, SuperExpr):
            return self._super_target(callee)
        if isinstance(callee, RefExpr) and isinstance(callee.node, TypeInfo):
            return "constructor", callee.node.fullname
        signature = self.signature(call)
        if signature is not None:
            return self._signature_target(signature, callee)
        if isinstance(callee, RefExpr) and isinstance(callee.node, FUNCTION_NODES):
            return "function", self.name_of(callee.node)
        return "unresolved", str(self.type_of(callee))

    def _signature_target(self, signature: CallableType, callee: Expression) -> tuple[str, str]:
        if signature.is_type_obj():
            return "constructor", signature.type_object().fullname
        definition = signature.definition
        if definition is not None:
            protocol_owned = isinstance(definition, FuncBase) and owner_is_protocol(definition)
            return ("protocol" if protocol_owned else "function"), self.name_of(definition)
        if isinstance(callee, RefExpr) and isinstance(callee.node, FUNCTION_NODES):
            return "function", self.name_of(callee.node)
        return "value", str(signature)

    def reference(self, expression: Expression) -> str | None:
        if isinstance(expression, RefExpr) and isinstance(expression.node, FUNCTION_NODES):
            return self.name_of(expression.node)
        if isinstance(expression, MemberExpr):
            found = self.type_of(expression)
            if isinstance(found, CallableType) and found.definition is not None:
                return self.name_of(found.definition)
        return None

    def receiver(self, call: CallExpr) -> Json | None:
        callee = call.callee
        if not isinstance(callee, MemberExpr):
            return None
        if isinstance(callee.expr, RefExpr) and isinstance(callee.expr.node, (MypyFile, TypeInfo)):
            return None
        return {"path": expression_path(callee.expr), "type": type_json(self.type_of(callee.expr))}

    @staticmethod
    def _super_target(callee: SuperExpr) -> tuple[str, str]:
        bases = callee.info.mro[1:] if callee.info is not None else []
        for base in bases:
            found = base.names.get(callee.name)
            if found is not None and found.node is not None:
                return "function", found.node.fullname
        return "unresolved", f"super().{callee.name}"


def concrete(found: ProperType) -> Instance | None:
    if isinstance(found, Instance) and not found.type.is_protocol:
        return found
    return None


def protocol(found: ProperType) -> Instance | None:
    if isinstance(found, Instance) and found.type.is_protocol:
        return found
    return None


class References(TraverserVisitor):
    def __init__(self, *, resolver: Resolver) -> None:
        super().__init__()
        self._resolver = resolver
        self.found: list[str] = []

    def visit_name_expr(self, o: NameExpr) -> None:
        self._note(o)

    def visit_member_expr(self, o: MemberExpr) -> None:
        self._note(o)
        super().visit_member_expr(o)

    def _note(self, expression: RefExpr) -> None:
        referenced = self._resolver.reference(expression)
        if referenced is not None:
            self.found.append(referenced)


class Docstrings:
    """The docstrings of modules, classes and functions, read with `ast` and found by module and line.

    A class or function is found by the line of its `def` or `class`, and by the line of its first decorator,
    as the two ways a line can name it.
    """

    def __init__(self, *, sources: dict[str, ast.Module]) -> None:
        self._modules = {module: ast.get_docstring(source) for module, source in sources.items()}
        self._definitions: dict[tuple[str, int], str] = {}
        for module, source in sources.items():
            for node in ast.walk(source):
                if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                doc = ast.get_docstring(node)
                if doc is None:
                    continue
                self._definitions[(module, node.lineno)] = doc
                if node.decorator_list:
                    self._definitions.setdefault((module, node.decorator_list[0].lineno), doc)

    def of(self, module: str, line: int) -> str | None:
        return self._definitions.get((module, line))

    def module(self, module: str) -> str | None:
        return self._modules.get(module)


class AnnotationCalls:
    """Calls inside `Annotated[...]` metadata, which mypy drops from its tree.

    Read with `ast` and resolved through each module's symbol table, so that a
    framework rule can find, for instance, the dependencies of a request handler.
    """

    def __init__(self, *, trees: dict[str, MypyFile], sources: dict[str, ast.Module]) -> None:
        self._aliases: dict[str, list[Json]] = {}
        self._parameters: dict[tuple[str, int], list[Json]] = {}
        for module, tree in trees.items():
            self._read_aliases(module, tree, sources[module])
        for module, tree in trees.items():
            self._read_parameters(module, tree, sources[module])

    def of(self, module: str, line: int) -> list[Json]:
        return self._parameters.get((module, line), [])

    def _read_aliases(self, module: str, tree: MypyFile, source: ast.Module) -> None:
        for statement in source.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and annotated(statement.value)
            ):
                self._aliases[f"{module}.{statement.targets[0].id}"] = self._calls(tree, statement.value)

    def _read_parameters(self, module: str, tree: MypyFile, source: ast.Module) -> None:
        for node in ast.walk(source):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            found: list[Json] = []
            for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                found.extend(self._annotation(tree, argument.annotation))
            if found:
                self._parameters[(module, node.lineno)] = found

    def _annotation(self, tree: MypyFile, annotation: ast.expr | None) -> list[Json]:
        if annotation is None:
            return []
        if annotated(annotation):
            return self._calls(tree, annotation)
        name = dotted(annotation)
        resolved = resolve(tree, name) if name else None
        return self._aliases.get(resolved, []) if resolved else []

    @staticmethod
    def _calls(tree: MypyFile, annotation: ast.expr) -> list[Json]:
        rows: list[Json] = []
        for node in ast.walk(annotation):
            if not isinstance(node, ast.Call):
                continue
            callee = dotted(node.func)
            rows.append(
                {
                    "target": resolve(tree, callee) if callee else None,
                    "references": [
                        resolved
                        for argument in node.args
                        if (name := dotted(argument)) and (resolved := resolve(tree, name) or placeholder(name))
                    ],
                }
            )
        return rows


def placeholder(name: str) -> str | None:
    """Stand for a name the module does not define, such as a parameter of a function that builds routes."""
    return None if "." in name else f"⟨{name}⟩"


def annotated(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Subscript) and dotted(expression.value) in ("Annotated", "typing.Annotated")


def dotted(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        base = dotted(expression.value)
        return f"{base}.{expression.attr}" if base else None
    return None


def resolve(tree: MypyFile, name: str) -> str | None:
    head, _, rest = name.partition(".")
    symbol = tree.names.get(head)
    if symbol is None or symbol.node is None:
        return None
    return f"{symbol.node.fullname}.{rest}" if rest else symbol.node.fullname


def class_kinds(info: TypeInfo) -> list[str]:
    ancestors = {base.fullname for base in info.mro}
    kinds = {
        "protocol": info.is_protocol,
        "enum": info.is_enum,
        "dataclass": "dataclass" in info.metadata,
        "pydantic": "pydantic.main.BaseModel" in ancestors,
        "exception": "builtins.BaseException" in ancestors,
        "abstract": info.is_abstract and not info.is_protocol,
        "namedtuple": info.tuple_type is not None,
    }
    return [kind for kind, applies in kinds.items() if applies]


def constructor_params(info: TypeInfo) -> list[Json]:
    for base in info.mro:
        found = base.names.get("__init__")
        if found is None:
            continue
        function = function_of(found.node)
        if base.fullname == "builtins.object" or function is None or not isinstance(function.type, CallableType):
            return []
        return signature_params(function.type, bound=True)
    return []


class Scan(TraverserVisitor):
    def __init__(self, *, resolver: Resolver, annotations: AnnotationCalls, docstrings: Docstrings) -> None:
        super().__init__()
        self._resolver = resolver
        self._annotations = annotations
        self._docstrings = docstrings
        self._module = ""
        self._functions_open: list[FuncDef] = []
        self.classes: list[Json] = []
        self.functions: list[Json] = []
        self.calls: list[Json] = []
        self.references: list[Json] = []
        self.bindings: list[Json] = []
        self.constructions: list[Json] = []
        self.decorated: dict[str, list[Json]] = {}
        self._raised: set[CallExpr] = set()

    def scan(self, tree: MypyFile) -> None:
        self._module = tree.fullname
        tree.accept(self)

    @property
    def _scope(self) -> str:
        return self._resolver.name_of(self._functions_open[-1]) if self._functions_open else self._module

    def visit_class_def(self, o: ClassDef) -> None:
        info = o.info
        self.classes.append(
            {
                "fullname": info.fullname,
                "module": self._module,
                "line": o.line,
                "kinds": class_kinds(info),
                "bases": [base.type.fullname for base in info.bases],
                "methods": [
                    self._resolver.name_of(symbol.node)
                    for symbol in info.names.values()
                    if isinstance(symbol.node, FUNCTION_NODES) and not symbol.plugin_generated
                ],
                "init": constructor_params(info),
                "doc": self._docstrings.of(self._module, o.line),
            }
        )
        if not info.is_protocol:
            for base in info.bases:
                if base.type.is_protocol:
                    self._bind(base.type.fullname, info.fullname, line=o.line)
        super().visit_class_def(o)

    def visit_decorator(self, o: Decorator) -> None:
        self._resolver.qualify(o.func, enclosing=self._functions_open[-1] if self._functions_open else None)
        self.decorated[self._resolver.name_of(o.func)] = [self._decorator(expression) for expression in o.decorators]
        super().visit_decorator(o)

    def visit_func_def(self, o: FuncDef) -> None:
        signature = o.type if isinstance(o.type, CallableType) else None
        fullname = self._resolver.qualify(o, enclosing=self._functions_open[-1] if self._functions_open else None)
        self.functions.append(
            {
                "fullname": fullname,
                "module": self._module,
                "line": o.line,
                "is_async": o.is_coroutine,
                "params": signature_params(signature, bound=is_method(o)) if signature else [],
                "returns": type_json(signature.ret_type if signature else None),
                "annotation_calls": self._annotations.of(self._module, o.line),
                "doc": self._docstrings.of(self._module, o.line),
            }
        )
        self._functions_open.append(o)
        super().visit_func_def(o)
        self._functions_open.pop()

    def visit_call_expr(self, o: CallExpr) -> None:
        kind, target = self._resolver.target(o)
        self.calls.append(
            {
                "caller": self._scope,
                "line": o.line,
                "kind": kind,
                "target": target,
                "receiver": self._resolver.receiver(o),
                "arguments": self._arguments(o),
                "raised": o in self._raised,
            }
        )
        for argument in o.args:
            referenced = self._resolver.reference(argument)
            if referenced is not None:
                self.references.append({"caller": self._scope, "line": o.line, "target": referenced, "via": target})
        self._bind_arguments(o)
        super().visit_call_expr(o)

    def visit_raise_stmt(self, o: RaiseStmt) -> None:
        if isinstance(o.expr, CallExpr):
            self._raised.add(o.expr)
        super().visit_raise_stmt(o)

    def visit_return_stmt(self, o: ReturnStmt) -> None:
        if self._functions_open and o.expr is not None:
            declared = self._functions_open[-1].type
            if isinstance(declared, CallableType):
                self._bind_types(get_proper_type(declared.ret_type), self._resolver.type_of(o.expr), line=o.line)
        super().visit_return_stmt(o)

    def visit_assignment_stmt(self, o: AssignmentStmt) -> None:
        if not self._functions_open and isinstance(o.rvalue, CallExpr):
            kind, target = self._resolver.target(o.rvalue)
            for lvalue in o.lvalues:
                if isinstance(lvalue, NameExpr):
                    self.constructions.append(
                        {
                            "name": lvalue.fullname,
                            "line": o.line,
                            "kind": kind,
                            "target": target,
                            "keywords": literal_keywords(o.rvalue),
                            "references": self._references_within(o.rvalue),
                        }
                    )
        super().visit_assignment_stmt(o)

    def _decorator(self, expression: Expression) -> Json:
        if not isinstance(expression, CallExpr):
            return {"target": self._resolver.reference(expression) or expression_path(expression)}
        _, target = self._resolver.target(expression)
        receiver = expression.callee.expr if isinstance(expression.callee, MemberExpr) else None
        return {
            "target": target,
            "receiver": module_variable(receiver) if receiver is not None else None,
            "receiver_param": receiver.name
            if isinstance(receiver, NameExpr) and receiver.name in self._parameters
            else None,
            "args": [
                template(argument)
                for argument, name in zip(expression.args, expression.arg_names, strict=True)
                if name is None
            ],
            "keywords": literal_keywords(expression),
            "references": self._references_within(expression),
        }

    def _references_within(self, expression: Expression) -> list[str]:
        collector = References(resolver=self._resolver)
        expression.accept(collector)
        return list(dict.fromkeys(collector.found))

    @property
    def _parameters(self) -> frozenset[str]:
        return (
            frozenset(name for name in self._functions_open[-1].arg_names if name)
            if self._functions_open
            else frozenset()
        )

    def _pairs(self, call: CallExpr) -> Iterator[tuple[CallableType, int, int]]:
        signature = self._resolver.signature(call)
        if signature is None:
            return
        formals = map_actuals_to_formals(
            call.arg_kinds,
            call.arg_names,
            signature.arg_kinds,
            signature.arg_names,
            lambda index: self._resolver.type_of(call.args[index]),
        )
        for formal, actuals in enumerate(formals):
            for actual in actuals:
                yield signature, formal, actual

    def _arguments(self, call: CallExpr) -> list[Json]:
        """What each parameter receives where the source says it plainly: a string, a name, a parameter passed on."""
        rows: list[Json] = []
        for signature, formal, actual in self._pairs(call):
            argument = call.args[actual]
            row = {
                "literal": literal(argument),
                "ref": self._resolver.reference(argument) or module_variable(argument),
                "from_param": argument.name
                if isinstance(argument, NameExpr) and argument.name in self._parameters
                else None,
            }
            if any(value is not None for value in row.values()):
                rows.append({"param": signature.arg_names[formal] or "", **row})
        return rows

    def _bind_arguments(self, call: CallExpr) -> None:
        for signature, formal, actual in self._pairs(call):
            self._bind_types(
                get_proper_type(signature.arg_types[formal]),
                self._resolver.type_of(call.args[actual]),
                line=call.line,
            )

    def _bind_types(self, declared: ProperType, actual: ProperType, *, line: int) -> None:
        wanted = protocol(declared)
        given = concrete(actual)
        if wanted is not None and given is not None:
            self._bind(wanted.type.fullname, given.type.fullname, line=line)

    def _bind(self, protocol_name: str, implementation: str, *, line: int) -> None:
        self.bindings.append(
            {"protocol": protocol_name, "implementation": implementation, "site": self._scope, "line": line}
        )


def package_trees(result: build.BuildResult, package: str) -> Iterator[tuple[str, MypyFile]]:
    for module, state in sorted(result.graph.items()):
        if (module == package or module.startswith(f"{package}.")) and state.tree is not None:
            yield module, state.tree


def main() -> None:
    package_dir, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    package = package_dir.name
    targets, options = process_options([str(package_dir)])
    options.export_types = True
    options.preserve_asts = True
    options.incremental = False
    options.cache_dir = os.devnull
    result = build.build(targets, options)
    trees = dict(package_trees(result, package))
    sources = {module: ast.parse(Path(tree.path).read_text(encoding="utf-8")) for module, tree in trees.items()}
    docstrings = Docstrings(sources=sources)
    scan = Scan(
        resolver=Resolver(types=result.types),
        annotations=AnnotationCalls(trees=trees, sources=sources),
        docstrings=docstrings,
    )
    for tree in trees.values():
        scan.scan(tree)
    functions = [{**row, "decorators": scan.decorated.get(str(row["fullname"]), [])} for row in scan.functions]
    output.write_text(
        json.dumps(
            {
                "package": package,
                "modules": [
                    {"name": module, "path": tree.path, "doc": docstrings.module(module)}
                    for module, tree in trees.items()
                ],
                "classes": scan.classes,
                "functions": functions,
                "calls": scan.calls,
                "references": scan.references,
                "bindings": scan.bindings,
                "constructions": scan.constructions,
                "type_errors": len(result.errors),
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
