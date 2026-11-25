import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from schematic.board import Board, LinkKind
from schematic.errors import UnknownNameError
from schematic.recording import Recording
from schematic.scan import FROZEN, ClassKind, ClassScan

VALUE_KINDS = frozenset(
    {ClassKind.PYDANTIC, ClassKind.ENUM, ClassKind.EXCEPTION, ClassKind.NAMEDTUPLE, ClassKind.PROTOCOL}
)
STANDARD_LIBRARY = frozenset(sys.stdlib_module_names) | {"builtins"}
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
    service: str
    methods: tuple[str, ...]


class Call(BaseModel):
    model_config = FROZEN

    source: int
    source_method: str
    target: int
    target_method: str
    count: int


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
    overview: str
    sheets: tuple[SheetSummary, ...]


class FunctionRef(BaseModel):
    """A function a flow ran: its sheet, and its part and method when the part is on the sheet asked for (else -1)."""

    model_config = FROZEN

    sheet: str
    part: int
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
        self._lines = {function.fullname: function.line for function in scan.functions}
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
        return SheetIndex(project=self.overview, overview=self.overview, sheets=tuple(summaries))

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

    def _resolve(self, caller: str) -> Iterator[tuple[str, str]]:
        for link in self._board.links(caller):
            if link.kind in (LinkKind.VALUE, LinkKind.TERMINAL):
                continue
            if link.kind in (LinkKind.CONSTRUCT, LinkKind.RAISE):
                built = self._classes.get(link.via or "")
                if built is not None and behaves(built):
                    yield built.fullname, BUILD
                continue
            part = self.part_of(link.target)
            yield part, self.member(part, link.target)

    def _libraries_of(self, caller: str) -> Iterator[str]:
        for link in self._board.links(caller):
            library = link.target.split(".")[0]
            if link.kind is LinkKind.TERMINAL and library not in STANDARD_LIBRARY:
                yield library

    def _package_sheet(self, sheet_id: str) -> Sheet:
        calls: Counter[tuple[str, str, str, str]] = Counter()
        inbound: Counter[tuple[str, str, str]] = Counter()
        outbound: Counter[tuple[str, str, str]] = Counter()
        for caller in self._callers:
            source_sheet = self.sheet_of(caller)
            source = self.part_of(caller)
            source_method = self.member(source, caller)
            if source_sheet == sheet_id:
                for library in self._libraries[caller]:
                    outbound[(source, source_method, f"library:{library}")] += 1
            for target, target_method in self._targets[caller]:
                target_sheet = self.sheet_of(target)
                if source_sheet == sheet_id == target_sheet and target != source:
                    calls[(source, source_method, target, target_method)] += 1
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
                Call(source=index[s], source_method=sm, target=index[t], target_method=tm, count=n)
                for (s, sm, t, tm), n in sorted(calls.items())
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
            for target_part, _ in self._targets[caller]:
                target = index.get(self.sheet_of(target_part))
                if target is not None and target != source:
                    calls[(source, target)] += 1
        return Sheet(
            id=self.overview,
            title=self.overview,
            parts=tuple(
                Part(id=found, title=found, kind=PartKind.PACKAGE, role=Role.PACKAGE, file="", service="", methods=())
                for found in self._ids
            ),
            calls=tuple(
                Call(source=s, source_method="", target=t, target_method="", count=n)
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

    def _services(self, ids: list[str], calls: Counter[tuple[str, str, str, str]]) -> dict[str, str]:
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
            row = FunctionRef(sheet=sheet, part=self._parts.get(sheet, -1), method="")
        else:
            part = self._sheets.part_of(name)
            position = self._parts.get(part, -1)
            row = FunctionRef(
                sheet=sheet, part=position, method=self._sheets.member(part, name) if position >= 0 else ""
            )
        self._refs[name] = len(self._rows)
        self._rows.append(row)
        return self._refs[name]

    def functions(self) -> tuple[FunctionRef, ...]:
        return tuple(self._rows)
