import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from schematic.board import Board, LinkKind
from schematic.errors import UnknownNameError
from schematic.recording import Recording
from schematic.scan import FROZEN, ClassKind, ClassScan, ParamKind

VALUE_KINDS = frozenset(
    {ClassKind.PYDANTIC, ClassKind.ENUM, ClassKind.EXCEPTION, ClassKind.NAMEDTUPLE, ClassKind.PROTOCOL}
)
STANDARD_LIBRARY = frozenset(sys.stdlib_module_names) | {"builtins"}
DATA_KINDS = frozenset(
    {ClassKind.PYDANTIC, ClassKind.DATACLASS, ClassKind.NAMEDTUPLE, ClassKind.ENUM, ClassKind.EXCEPTION}
)
SHAPELESS_KINDS = frozenset({ClassKind.ENUM, ClassKind.PROTOCOL})
MODULE_SCOPE = "<module>"
BUILD = "new"
ROOT_SHEET = "__init__"
SHARED = "Shared by services"
DIRECT = "Reached from other sheets only"


class PartKind(StrEnum):
    CLASS = "class"
    MODULE = "module"
    PACKAGE = "package"


class Role(StrEnum):
    SERVICE = "service"
    INTERNAL = "internal"
    MODULE = "module"
    PACKAGE = "package"


class Part(BaseModel):
    """A class with behaviour, a module of functions and data types, or on the overview a whole sheet.

    `methods` are those that take part in a call, in the order of the source file.
    `service` names the service whose calls alone reach the part, or says that several do.
    """

    model_config = FROZEN

    id: str
    title: str
    kind: PartKind
    role: Role
    file: str
    path: str
    service: str
    methods: tuple[str, ...]


class Collaborator(BaseModel):
    """A constructor parameter of a class, with the parts its type names."""

    model_config = FROZEN

    name: str
    type: str
    parts: tuple[str, ...]


class Member(BaseModel):
    model_config = FROZEN

    name: str
    line: int
    doc: str | None


class PartDetail(BaseModel):
    """What a sheet does not draw about one part: where it is, how it is built and held, all its members."""

    model_config = FROZEN

    id: str
    title: str
    kind: PartKind
    role: Role
    path: str
    line: int | None
    doc: str | None
    kinds: tuple[ClassKind, ...]
    implements: tuple[str, ...]
    built_with: tuple[Collaborator, ...]
    held_by: tuple[str, ...]
    members: tuple[Member, ...]
    raises: tuple[str, ...]
    types: tuple[str, ...]


class Call(BaseModel):
    """Calls from one method to another; `lines` are where they stand in the source part's file."""

    model_config = FROZEN

    source: int
    source_method: str
    target: int
    target_method: str
    count: int
    lines: tuple[int, ...]


class Inbound(BaseModel):
    """Calls from another sheet into a method of a part on this sheet."""

    model_config = FROZEN

    sheet: str
    target: int
    target_method: str
    count: int


class Outbound(BaseModel):
    """Calls from a method of a part to another sheet (`sheet:<id>`) or to a library (`library:<name>`)."""

    model_config = FROZEN

    source: int
    source_method: str
    target: str
    count: int


class Sheet(BaseModel):
    model_config = FROZEN

    id: str
    title: str
    parts: tuple[Part, ...]
    calls: tuple[Call, ...]
    inbound: tuple[Inbound, ...]
    outbound: tuple[Outbound, ...]


class SheetSummary(BaseModel):
    model_config = FROZEN

    id: str
    title: str
    parts: int


class SheetIndex(BaseModel):
    model_config = FROZEN

    project: str
    root: str
    overview: str
    sheets: tuple[SheetSummary, ...]


class FunctionRef(BaseModel):
    """A function a flow ran: its sheet, and its part and method when the part is on the sheet asked for (else "")."""

    model_config = FROZEN

    sheet: str
    part: str
    method: str


class FlowPath(BaseModel):
    """One recorded flow; `edges` are (caller, callee, count) into the function table, a caller of -1 is the test."""

    model_config = FROZEN

    test: str
    name: str
    outcome: str
    edges: tuple[tuple[int, int, int], ...]
    raises: tuple[tuple[int, str, bool], ...]


class SheetFlows(BaseModel):
    model_config = FROZEN

    functions: tuple[FunctionRef, ...]
    flows: tuple[FlowPath, ...]


class Field(BaseModel):
    model_config = FROZEN

    name: str
    type: str


class DataType(BaseModel):
    """A class of the package that a call sends or returns.

    `data` marks a value class (a model, dataclass, named tuple, enum or exception), whose fields are listed and
    followed; a part or a Protocol is named without its insides.
    """

    model_config = FROZEN

    id: str
    title: str
    kinds: tuple[ClassKind, ...]
    doc: str | None
    data: bool
    fields: tuple[Field, ...]


class Parameter(BaseModel):
    """One parameter of a call; `types` are the package's classes its type names."""

    model_config = FROZEN

    name: str
    kind: ParamKind
    type: str
    types: tuple[str, ...]


class Signature(BaseModel):
    """What a call sends (its parameters) and what comes back (its return type)."""

    model_config = FROZEN

    function: str
    parameters: tuple[Parameter, ...]
    returns: str
    return_types: tuple[str, ...]
    doc: str | None


class CallData(BaseModel):
    model_config = FROZEN

    signatures: tuple[Signature, ...]
    types: tuple[DataType, ...]


def behaves(found: ClassScan | None) -> bool:
    return found is not None and not (set(found.kinds) & VALUE_KINDS)


def outer(name: str) -> str:
    return name.partition(".<locals>.")[0]


class Sheets:
    """The drawings of a package: an overview of its top-level modules and packages, and a sheet for each of them."""

    def __init__(self, *, board: Board, recording: Recording | None) -> None:
        self._board = board
        self._recording = recording
        scan = board.scan
        self._classes = {found.fullname: found for found in scan.classes}
        self._modules = {module.name: Path(module.path).name for module in scan.modules}
        self._paths = {module.name: module.path for module in scan.modules}
        self._docs = {module.name: module.doc for module in scan.modules}
        self._lines = {function.fullname: function.line for function in scan.functions}
        self._functions = {function.fullname: function for function in scan.functions}
        self._callers = (*(function.fullname for function in scan.functions), *sorted(self._modules))
        self._targets = {caller: tuple(self._resolve(caller)) for caller in self._callers}
        self._libraries = {caller: tuple(self._libraries_of(caller)) for caller in self._callers}
        self._ids = tuple(sorted({self.sheet_of(function.fullname) for function in scan.functions}))

    @property
    def overview(self) -> str:
        return self._board.package

    def index(self) -> SheetIndex:
        summaries = [SheetSummary(id=self.overview, title=self.overview, parts=len(self._ids))]
        summaries.extend(SheetSummary(id=found, title=found, parts=len(self.sheet(found).parts)) for found in self._ids)
        return SheetIndex(project=self.overview, root=self._root(), overview=self.overview, sheets=tuple(summaries))

    def sheet(self, sheet_id: str) -> Sheet:
        if sheet_id == self.overview:
            return self._overview()
        if sheet_id not in self._ids:
            raise UnknownNameError(f"no sheet {sheet_id!r}")
        return self._package_sheet(sheet_id)

    def flows(self, sheet_id: str) -> SheetFlows:
        sheet = self.sheet(sheet_id)
        if self._recording is None:
            return SheetFlows(functions=(), flows=())
        table = FunctionTable(sheets=self, sheet=sheet, overview=sheet_id == self.overview)
        paths = [
            FlowPath(
                test=flow.test,
                name=flow.name,
                outcome=flow.outcome,
                edges=tuple(
                    (table.ref(edge.caller) if edge.caller else -1, table.ref(edge.callee), edge.count)
                    for edge in flow.ordered_edges()
                ),
                raises=tuple((table.ref(step.function), step.exception, step.origin) for step in flow.raises),
            )
            for flow in self._recording.flows
            if sheet_id == self.overview or any(self.sheet_of(edge.callee) == sheet_id for edge in flow.edges)
        ]
        return SheetFlows(functions=table.functions(), flows=tuple(paths))

    @property
    def ids(self) -> tuple[str, ...]:
        return self._ids

    def doc_of(self, part_id: str) -> str | None:
        found = self._classes.get(part_id)
        if found is not None:
            return found.doc
        return self._docs.get(part_id) or self._docs.get(f"{self.overview}.{part_id}")

    def detail(self, part_id: str) -> PartDetail:
        if part_id in self._ids:
            return PartDetail(
                id=part_id,
                title=part_id,
                kind=PartKind.PACKAGE,
                role=Role.PACKAGE,
                path=self._paths.get(f"{self.overview}.{part_id}", ""),
                line=None,
                doc=self.doc_of(part_id),
                kinds=(),
                implements=(),
                built_with=(),
                held_by=(),
                members=(),
                raises=(),
                types=(),
            )
        found = self._classes.get(part_id)
        if not behaves(found) and part_id not in self._modules:
            raise UnknownNameError(f"no part {part_id!r}")
        functions = sorted(
            (
                function
                for function in self._board.scan.functions
                if outer(function.fullname) == function.fullname and self.part_of(function.fullname) == part_id
            ),
            key=lambda function: function.line,
        )
        raises = sorted(
            {
                link.via
                for function in functions
                for link in self._board.links(function.fullname)
                if link.kind is LinkKind.RAISE and link.via is not None
            }
        )
        return PartDetail(
            id=part_id,
            title=part_id.rpartition(".")[2],
            kind=PartKind.CLASS if found is not None else PartKind.MODULE,
            role=self._role(part_id),
            path=self._paths[self.module_of(part_id)],
            line=found.line if found is not None else None,
            doc=self.doc_of(part_id),
            kinds=found.kinds if found is not None else (),
            implements=self._board.protocols_of(part_id),
            built_with=tuple(
                Collaborator(name=wire.name, type=wire.type, parts=wire.components)
                for wire in (self._board.wires(part_id) if found is not None else ())
            ),
            held_by=tuple(f"{holder.component}.{holder.wire.name}" for holder in self._board.holders(part_id)),
            members=tuple(
                Member(name=self.member(part_id, function.fullname), line=function.line, doc=function.doc)
                for function in functions
            ),
            raises=tuple(raises),
            types=tuple(c.fullname for c in self._board.scan.classes if c.module == part_id and not behaves(c)),
        )

    def data(self, calls: Sequence[str]) -> CallData:
        """The data that calls move, each named `<part>|<method>`, with every class of the package it is made of.

        A construction (`new`) sends the class's constructor parameters, also generated or inherited ones, and
        returns the class.
        """
        signatures = [self._signature(*call.partition("|")[::2]) for call in calls]
        pending = [ref for signature in signatures for ref in self._sent(signature)]
        types: dict[str, DataType] = {}
        while pending:
            ref = pending.pop(0)
            if ref in types:
                continue
            found_class = self._classes[ref]
            data = bool(set(found_class.kinds) & DATA_KINDS)
            shapeless = bool(set(found_class.kinds) & SHAPELESS_KINDS)
            types[ref] = DataType(
                id=ref,
                title=ref.rpartition(".")[2],
                kinds=found_class.kinds,
                doc=found_class.doc,
                data=data,
                fields=() if shapeless else tuple(Field(name=p.name, type=p.type.text) for p in found_class.init),
            )
            if data and not shapeless:
                pending.extend(ref for p in found_class.init for ref in self._known(p.type.refs))
        return CallData(signatures=tuple(signatures), types=tuple(types.values()))

    def sheet_of(self, name: str) -> str:
        parts = self.module_of(outer(name)).split(".")
        return parts[1] if len(parts) > 1 else ROOT_SHEET

    def module_of(self, name: str) -> str:
        while name and name not in self._modules:
            name = name.rpartition(".")[0]
        return name

    def part_of(self, name: str) -> str:
        name = outer(name)
        if name in self._modules:
            return name
        owner = self._board.owner(name)
        return owner.fullname if owner is not None and behaves(owner) else self.module_of(name)

    def member(self, part: str, name: str) -> str:
        name = outer(name)
        if name == part:
            return MODULE_SCOPE
        label = name[len(part) + 1 :] if name.startswith(f"{part}.") else name.rpartition(".")[2]
        return BUILD if label == "__init__" else label

    def _resolve(self, caller: str) -> Iterator[tuple[str, str, int | None]]:
        for link in self._board.links(caller):
            if link.kind in (LinkKind.VALUE, LinkKind.TERMINAL):
                continue
            if link.kind in (LinkKind.CONSTRUCT, LinkKind.RAISE):
                built = self._classes.get(link.via or "")
                if built is not None and behaves(built):
                    yield built.fullname, BUILD, link.line
                continue
            part = self.part_of(link.target)
            yield part, self.member(part, link.target), link.line

    def _libraries_of(self, caller: str) -> Iterator[str]:
        for link in self._board.links(caller):
            library = link.target.split(".")[0]
            if link.kind is LinkKind.TERMINAL and library not in STANDARD_LIBRARY:
                yield library

    def _package_sheet(self, sheet_id: str) -> Sheet:
        calls: dict[tuple[str, str, str, str], list[int | None]] = defaultdict(list)
        inbound: Counter[tuple[str, str, str]] = Counter()
        outbound: Counter[tuple[str, str, str]] = Counter()
        for caller in self._callers:
            source_sheet = self.sheet_of(caller)
            source = self.part_of(caller)
            source_method = self.member(source, caller)
            if source_sheet == sheet_id:
                for library in self._libraries[caller]:
                    outbound[(source, source_method, f"library:{library}")] += 1
            for target, target_method, line in self._targets[caller]:
                target_sheet = self.sheet_of(target)
                if source_sheet == sheet_id == target_sheet and target != source:
                    calls[(source, source_method, target, target_method)].append(line)
                elif source_sheet == sheet_id != target_sheet:
                    outbound[(source, source_method, f"sheet:{target_sheet}")] += 1
                elif target_sheet == sheet_id != source_sheet:
                    inbound[(source_sheet, target, target_method)] += 1
        methods: dict[str, set[str]] = defaultdict(set)
        for source, source_method, target, target_method in calls:
            methods[source].add(source_method)
            methods[target].add(target_method)
        for _, target, target_method in inbound:
            methods[target].add(target_method)
        for source, source_method, _ in outbound:
            methods[source].add(source_method)
        ids = sorted(methods)
        index = {part: position for position, part in enumerate(ids)}
        services = self._services(ids, calls)
        parts = tuple(
            Part(
                id=part,
                title=part.rpartition(".")[2],
                kind=PartKind.CLASS if part in self._classes else PartKind.MODULE,
                role=self._role(part),
                file=self._modules[self.module_of(part)],
                path=self._paths[self.module_of(part)],
                service=services[part],
                methods=tuple(sorted(methods[part], key=lambda method: (self._line(part, method), method))),
            )
            for part in ids
        )
        return Sheet(
            id=sheet_id,
            title=sheet_id,
            parts=parts,
            calls=tuple(
                Call(
                    source=index[s],
                    source_method=sm,
                    target=index[t],
                    target_method=tm,
                    count=len(lines),
                    lines=tuple(sorted({line for line in lines if line is not None})),
                )
                for (s, sm, t, tm), lines in sorted(calls.items())
            ),
            inbound=tuple(
                Inbound(sheet=sheet, target=index[t], target_method=tm, count=n)
                for (sheet, t, tm), n in sorted(inbound.items())
            ),
            outbound=tuple(
                Outbound(source=index[s], source_method=sm, target=target, count=n)
                for (s, sm, target), n in sorted(outbound.items())
            ),
        )

    def _overview(self) -> Sheet:
        index = {sheet_id: position for position, sheet_id in enumerate(self._ids)}
        calls: Counter[tuple[int, int]] = Counter()
        outbound: Counter[tuple[int, str]] = Counter()
        for caller in self._callers:
            source = index.get(self.sheet_of(caller))
            if source is None:
                continue
            for library in self._libraries[caller]:
                outbound[(source, f"library:{library}")] += 1
            for target_part, _, _ in self._targets[caller]:
                target = index.get(self.sheet_of(target_part))
                if target is not None and target != source:
                    calls[(source, target)] += 1
        return Sheet(
            id=self.overview,
            title=self.overview,
            parts=tuple(
                Part(
                    id=found,
                    title=found,
                    kind=PartKind.PACKAGE,
                    role=Role.PACKAGE,
                    file="",
                    path=self._paths.get(f"{self.overview}.{found}", ""),
                    service="",
                    methods=(),
                )
                for found in self._ids
            ),
            calls=tuple(
                Call(source=s, source_method="", target=t, target_method="", count=n, lines=())
                for (s, t), n in sorted(calls.items())
            ),
            inbound=(),
            outbound=tuple(
                Outbound(source=s, source_method="", target=target, count=n)
                for (s, target), n in sorted(outbound.items())
            ),
        )

    def _role(self, part: str) -> Role:
        if part not in self._classes:
            return Role.MODULE
        return Role.SERVICE if self._board.protocols_of(part) else Role.INTERNAL

    def _line(self, part: str, method: str) -> int:
        if method == MODULE_SCOPE:
            return 0
        name = f"{part}.__init__" if method == BUILD else f"{part}.{method}"
        return self._lines.get(name, sys.maxsize)

    def _signature(self, part: str, method: str) -> Signature:
        label = f"{part}.{method}"
        built = self._classes.get(part)
        if method == BUILD and built is not None:
            return Signature(
                function=label,
                parameters=tuple(
                    Parameter(name=p.name, kind=p.kind, type=p.type.text, types=self._known(p.type.refs))
                    for p in built.init
                ),
                returns=part,
                return_types=(part,),
                doc=built.doc,
            )
        name = self._board.method(part, method) if built is not None else label
        found = self._functions.get(name or "")
        if found is None:
            raise UnknownNameError(f"no method {method!r} on {part!r}")
        return Signature(
            function=label,
            parameters=tuple(
                Parameter(name=p.name, kind=p.kind, type=p.type.text, types=self._known(p.type.refs))
                for p in found.params
            ),
            returns=found.returns.text,
            return_types=self._known(found.returns.refs),
            doc=found.doc,
        )

    def _known(self, refs: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ref for ref in refs if ref in self._classes))

    @staticmethod
    def _sent(signature: Signature) -> Iterator[str]:
        for parameter in signature.parameters:
            yield from parameter.types
        yield from signature.return_types

    def _root(self) -> str:
        """The folder that holds the package: the parent of the package's own directory."""
        return str(Path(self._paths[self.overview]).parent.parent)

    def _services(self, ids: list[str], calls: Iterable[tuple[str, str, str, str]]) -> dict[str, str]:
        """Group each part under the one service that reaches it, or mark it as shared or reached directly."""
        onward: dict[str, set[str]] = defaultdict(set)
        for source, _, target, _ in calls:
            onward[source].add(target)
        services = [part for part in ids if self._role(part) is Role.SERVICE]
        reached_by: dict[str, set[str]] = defaultdict(set)
        for service in services:
            seen, pending = {service}, [service]
            while pending:
                for found in onward[pending.pop()] - seen:
                    seen.add(found)
                    pending.append(found)
            for found in seen - {service}:
                reached_by[found].add(service)
        groups = {}
        for part in ids:
            owners = reached_by.get(part, set())
            if part in services:
                groups[part] = part.rpartition(".")[2]
            elif len(owners) == 1:
                groups[part] = next(iter(owners)).rpartition(".")[2]
            else:
                groups[part] = SHARED if owners else DIRECT
        return groups


class FunctionTable:
    """Numbers the functions that flows ran, once each, as seen from one sheet."""

    def __init__(self, *, sheets: Sheets, sheet: Sheet, overview: bool) -> None:
        self._sheets = sheets
        self._overview = overview
        self._parts = {part.id: position for position, part in enumerate(sheet.parts)}
        self._refs: dict[str, int] = {}
        self._rows: list[FunctionRef] = []

    def ref(self, name: str) -> int:
        found = self._refs.get(name)
        if found is not None:
            return found
        sheet = self._sheets.sheet_of(name)
        if self._overview:
            row = FunctionRef(sheet=sheet, part=sheet if sheet in self._parts else "", method="")
        else:
            part = self._sheets.part_of(name)
            on = part in self._parts
            row = FunctionRef(
                sheet=sheet, part=part if on else "", method=self._sheets.member(part, name) if on else ""
            )
        self._refs[name] = len(self._rows)
        self._rows.append(row)
        return self._refs[name]

    def functions(self) -> tuple[FunctionRef, ...]:
        return tuple(self._rows)
