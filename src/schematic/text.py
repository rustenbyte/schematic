from collections import Counter, defaultdict
from collections.abc import Iterable

from schematic.board import Board, LinkKind
from schematic.recording import Flow, Recording
from schematic.scan import CallKind, ClassKind
from schematic.trace import FlowCoverage, TraceNode

RESOLVED_KINDS = frozenset({CallKind.FUNCTION, CallKind.CONSTRUCTOR, CallKind.PROTOCOL})


class TextView:
    """The board, its traces and its flows as plain text for the terminal."""

    def __init__(self, *, board: Board) -> None:
        self._board = board
        self._prefix = f"{board.package}."

    def short(self, name: str) -> str:
        return name.removeprefix(self._prefix)

    def summary(self) -> list[str]:
        scan = self._board.scan
        kinds = Counter(call.kind for call in scan.calls)
        resolved = sum(kinds[kind] for kind in RESOLVED_KINDS)
        protocols = self._board.protocols()
        unbound = [found.fullname for found in protocols if not self._board.implementations(found.fullname)]
        lines = [
            f"{scan.package}: {len(scan.modules)} modules, {len(scan.classes)} classes, "
            f"{len(scan.functions)} functions",
            f"calls      {len(scan.calls)}: {resolved} resolved ({percent(resolved, len(scan.calls))}), "
            f"{kinds[CallKind.PROTOCOL]} through a Protocol, {kinds[CallKind.VALUE]} to callables passed as values, "
            f"{kinds[CallKind.UNRESOLVED]} unresolved",
            f"protocols  {len(protocols)}: {len(protocols) - len(unbound)} bound to an implementation",
            f"routes     {len(self._board.routes.all())}",
            f"mypy       {scan.type_errors} error lines",
        ]
        lines.extend(f"unbound    {self.short(name)}" for name in unbound)
        return lines

    def routes(self) -> list[str]:
        routes = self._board.routes.all()
        width = max((len(route.label) for route in routes), default=0)
        return [f"{route.label:<{width}}  {self.short(route.handler)}" for route in routes]

    def tree(self, node: TraceNode) -> list[str]:
        lines = [self._label(node)]
        lines.extend(self._branches(node.children, indent=""))
        return lines

    def chip(self, component: str) -> list[str]:
        board = self._board
        found = board.component(component)
        kinds = f"  [{', '.join(found.kinds)}]" if found.kinds else ""
        lines = [f"{self.short(found.fullname)}  {found.module}:{found.line}{kinds}"]
        if ClassKind.PROTOCOL in found.kinds:
            lines.extend(f"bound to     {self.short(name)}" for name in board.implementations(found.fullname))
        lines.extend(f"implements   {self.short(name)}" for name in board.protocols_of(found.fullname))
        for wire in board.wires(found.fullname):
            ends = ", ".join(self.short(name) for name in wire.components)
            lines.append(f"wire         {wire.name}: {self.short_type(wire.type)}" + (f"  → {ends}" if ends else ""))
        pins = [name.rpartition(".")[2] for name in found.methods]
        public = [name for name in pins if not name.startswith("_")]
        if public:
            lines.append(f"pins         {', '.join(public)}")
        lines.extend(
            f"held by      {self.short(holder.component)}.{holder.wire.name}"
            for holder in board.holders(found.fullname)
        )
        callers = Counter(self._place(call.caller) for call in board.callers(found.fullname))
        lines.extend(f"called from  {place}  ×{count}" for place, count in sorted(callers.items()))
        return lines

    def flows(self, recording: Recording) -> list[str]:
        lines = [f"{len(recording.flows)} flows, pytest exit code {recording.pytest_exit_code}"]
        for flow in recording.flows:
            routes = [route.label for name in sorted(flow.functions()) for route in self._board.routes.of_handler(name)]
            raised = sorted({self.short(step.exception) for step in flow.raises if step.origin})
            lines.append(
                f"{flow.outcome:<7} {flow.name}  ({len(flow.functions())} functions"
                + (f"; {', '.join(routes)}" if routes else "")
                + (f"; raises {', '.join(raised)}" if raised else "")
                + ")"
            )
        return lines

    def flow(self, flow: Flow, roots: Iterable[TraceNode], coverage: FlowCoverage) -> list[str]:
        lines = [f"{flow.name}  [{flow.outcome}, {flow.seconds:.3f} s]", f"test  {flow.test}"]
        for root in roots:
            lines.extend(self.tree(root))
        if not coverage.handlers:
            return lines
        lines.append("")
        lines.append(
            f"static reach of {', '.join(self.short(name) for name in coverage.handlers)}: "
            f"{len(coverage.reachable)} functions, {len(coverage.taken)} taken, {len(coverage.missed)} not taken"
        )
        missed: dict[str, list[str]] = defaultdict(list)
        for name in sorted(coverage.missed):
            owner, _, function = name.rpartition(".")
            missed[self.short(owner)].append(function)
        lines.extend(f"  not taken  {owner}: {', '.join(functions)}" for owner, functions in missed.items())
        return lines

    def short_type(self, text: str) -> str:
        return text.replace(self._prefix, "")

    def _place(self, caller: str) -> str:
        owner = self._board.owner(caller)
        return self.short(owner.fullname if owner else caller.rpartition(".")[0])

    def _branches(self, children: tuple[TraceNode, ...], *, indent: str) -> list[str]:
        lines: list[str] = []
        for index, child in enumerate(children):
            last = index == len(children) - 1
            lines.append(f"{indent}{'└─ ' if last else '├─ '}{self._label(child)}")
            lines.extend(self._branches(child.children, indent=f"{indent}{'   ' if last else '│  '}"))
        return lines

    def _label(self, node: TraceNode) -> str:
        name = self.short(node.name)
        label = {
            LinkKind.ENTRY: name + (f"  ← {node.via}" if node.via else ""),
            LinkKind.CALL: name,
            LinkKind.DISPATCH: f"{name}  ⇐ {self.short(node.via or '').rpartition('.')[0]}",
            LinkKind.HANDOFF: f"↪ {name}  (passed to {self.short(node.via or '')})",
            LinkKind.DEPEND: f"◇ {name}",
            LinkKind.CONSTRUCT: f"new {self.short(node.via or node.name)}",
            LinkKind.RAISE: f"raises {self.short(node.via or node.name)}",
            LinkKind.TERMINAL: f"⏚ {name}: {', '.join(node.detail)}",
            LinkKind.VALUE: f"? {name}",
        }[node.kind]
        if node.count is not None and node.count > 1:
            label += f"  ×{node.count}"
        if node.kind is not LinkKind.TERMINAL and node.detail:
            label += "  " + "; ".join(f"✗ {self.short(event)}" for event in node.detail)
        if node.repeat:
            label += "  ↑"
        return label


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "–"
