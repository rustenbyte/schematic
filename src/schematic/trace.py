from collections import defaultdict

from pydantic import BaseModel

from schematic.board import Board, Link, LinkKind
from schematic.recording import Flow, FlowEdge
from schematic.scan import FROZEN

LEAF_KINDS = frozenset({LinkKind.TERMINAL, LinkKind.VALUE})
QUIET_MODULES = frozenset({"builtins"})


class TraceNode(BaseModel):
    """One function in a trace. `repeat` marks a function already expanded above, or a cycle back into the path."""

    model_config = FROZEN

    name: str
    kind: LinkKind
    line: int | None = None
    via: str | None = None
    count: int | None = None
    detail: tuple[str, ...] = ()
    repeat: bool = False
    children: tuple[TraceNode, ...] = ()


class StaticTrace:
    """Every path a call can take, as the types resolve it: what a function may reach."""

    def __init__(self, *, board: Board) -> None:
        self._board = board

    def tree(self, root: str) -> TraceNode:
        return self._node(Link(kind=LinkKind.ENTRY, target=root), expanded=set(), path=frozenset())

    def reach(self, root: str) -> frozenset[str]:
        reached = {root}
        pending = [root]
        while pending:
            for link in self._board.links(pending.pop()):
                if link.kind not in LEAF_KINDS and link.target not in reached:
                    reached.add(link.target)
                    pending.append(link.target)
        return frozenset(name for name in reached if self._board.has_function(name))

    def _node(self, link: Link, *, expanded: set[str], path: frozenset[str]) -> TraceNode:
        name = link.target
        if name in path or name in expanded:
            return TraceNode(name=name, kind=link.kind, line=link.line, via=link.via, repeat=True)
        expanded.add(name)
        links = self._board.links(name)
        children = [
            self._node(child, expanded=expanded, path=path | {name})
            for child in first_per_target(links)
            if child.kind not in LEAF_KINDS
        ]
        children.extend(terminal_groups(links))
        return TraceNode(name=name, kind=link.kind, line=link.line, via=link.via, children=tuple(children))


def first_per_target(links: tuple[Link, ...]) -> tuple[Link, ...]:
    seen: dict[tuple[LinkKind, str, str | None], Link] = {}
    for link in links:
        seen.setdefault((link.kind, link.target, link.via), link)
    return tuple(seen.values())


def terminal_groups(links: tuple[Link, ...]) -> list[TraceNode]:
    """Group the calls that leave the package by their top-level module: the terminals at the edge of the board."""
    grouped: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link.kind is not LinkKind.TERMINAL:
            continue
        module, _, rest = link.target.partition(".")
        if module not in QUIET_MODULES:
            grouped[module].add(rest or module)
    return [
        TraceNode(name=module, kind=LinkKind.TERMINAL, detail=tuple(sorted(names)))
        for module, names in sorted(grouped.items())
    ]


class FlowTrace:
    """The path one recorded flow took, in the order its calls first happened."""

    def __init__(self, *, flow: Flow) -> None:
        self._flow = flow
        self._children: dict[str | None, list[FlowEdge]] = defaultdict(list)
        for edge in flow.ordered_edges():
            self._children[edge.caller].append(edge)
        self._events: dict[str, list[str]] = defaultdict(list)
        for step in flow.raises:
            verb = "raises" if step.origin else "passes on"
            self._events[step.function].append(f"{verb} {step.exception}")
        for handling in flow.handled:
            self._events[handling.function].append(f"handles {handling.exception}")

    def roots(self) -> tuple[TraceNode, ...]:
        expanded: set[str] = set()
        return tuple(self._node(edge, expanded=expanded, path=frozenset()) for edge in self._children[None])

    def _node(self, edge: FlowEdge, *, expanded: set[str], path: frozenset[str]) -> TraceNode:
        name = edge.callee
        kind = LinkKind.ENTRY if edge.caller is None else LinkKind.CALL
        events = tuple(self._events.get(name, ()))
        if name in path or name in expanded:
            return TraceNode(name=name, kind=kind, via=edge.via, count=edge.count, detail=events, repeat=True)
        expanded.add(name)
        children = tuple(
            self._node(child, expanded=expanded, path=path | {name}) for child in self._children.get(name, ())
        )
        return TraceNode(name=name, kind=kind, via=edge.via, count=edge.count, detail=events, children=children)


class FlowCoverage(BaseModel):
    """What a flow took of the paths that the routes it entered may take."""

    model_config = FROZEN

    handlers: tuple[str, ...]
    reachable: frozenset[str]
    taken: frozenset[str]

    @property
    def missed(self) -> frozenset[str]:
        return self.reachable - self.taken


def flow_coverage(board: Board, flow: Flow) -> FlowCoverage:
    functions = flow.functions()
    handlers = tuple(sorted(name for name in functions if board.routes.of_handler(name)))
    trace = StaticTrace(board=board)
    reachable: frozenset[str] = frozenset().union(*(trace.reach(handler) for handler in handlers))
    return FlowCoverage(handlers=handlers, reachable=reachable, taken=reachable & functions)
