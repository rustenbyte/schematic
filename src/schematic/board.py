from collections import defaultdict
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel

from schematic.errors import AmbiguousNameError, UnknownNameError
from schematic.routes import FastApiRoutes
from schematic.scan import FROZEN, CallKind, CallSite, ClassKind, ClassScan, FunctionScan, StaticScan


class LinkKind(StrEnum):
    CALL = "call"
    DISPATCH = "dispatch"
    HANDOFF = "handoff"
    DEPEND = "depend"
    CONSTRUCT = "construct"
    RAISE = "raise"
    TERMINAL = "terminal"
    VALUE = "value"
    ENTRY = "entry"


class Link(BaseModel):
    """A static step out of a function.

    `via` names the Protocol method of a dispatch, the function that receives a handed-off one,
    or the class that a construction or a raise builds.
    """

    model_config = FROZEN

    kind: LinkKind
    target: str
    line: int | None = None
    via: str | None = None


class Wire(BaseModel):
    """A constructor parameter: how a component is connected to the parts it is built with."""

    model_config = FROZEN

    name: str
    type: str
    components: tuple[str, ...]


class Holder(BaseModel):
    model_config = FROZEN

    component: str
    wire: Wire


class Board:
    """The architecture of one package: its components, their wiring, and every resolved step between functions."""

    def __init__(self, *, scan: StaticScan, routes: FastApiRoutes) -> None:
        self.scan = scan
        self.routes = routes
        self._classes = {found.fullname: found for found in scan.classes}
        self._functions = {found.fullname: found for found in scan.functions}
        self._calls: dict[str, list[CallSite]] = defaultdict(list)
        for call in scan.calls:
            self._calls[call.caller].append(call)
        self._passes: dict[str, list[Link]] = defaultdict(list)
        for reference in scan.references:
            if self.internal(reference.target):
                self._passes[reference.caller].append(
                    Link(kind=LinkKind.HANDOFF, target=reference.target, line=reference.line, via=reference.via)
                )
        implementations: dict[str, set[str]] = defaultdict(set)
        for binding in scan.bindings:
            if self.internal(binding.protocol):
                implementations[binding.protocol].add(binding.implementation)
        self._implementations = {protocol: tuple(sorted(found)) for protocol, found in implementations.items()}
        self._protocols_of: dict[str, list[str]] = defaultdict(list)
        for protocol, found in self._implementations.items():
            for implementation in found:
                self._protocols_of[implementation].append(protocol)

    @property
    def package(self) -> str:
        return self.scan.package

    def internal(self, name: str) -> bool:
        return name == self.package or name.startswith(f"{self.package}.")

    def components(self) -> tuple[ClassScan, ...]:
        return tuple(self._classes.values())

    def protocols(self) -> tuple[ClassScan, ...]:
        return tuple(found for found in self._classes.values() if ClassKind.PROTOCOL in found.kinds)

    def component(self, query: str) -> ClassScan:
        return self._classes[find(query, self._classes)]

    def function(self, query: str) -> FunctionScan:
        return self._functions[find(query, self._functions)]

    def has_function(self, name: str) -> bool:
        return name in self._functions

    def owner(self, function: str) -> ClassScan | None:
        return self._classes.get(function.rpartition(".")[0])

    def implementations(self, protocol: str) -> tuple[str, ...]:
        return self._implementations.get(protocol, ())

    def protocols_of(self, component: str) -> tuple[str, ...]:
        return tuple(self._protocols_of.get(component, ()))

    def method(self, component: str, name: str) -> str | None:
        """Find the method a component runs for `name`, following its bases within the package."""
        pending = [component]
        seen: set[str] = set()
        while pending:
            current = self._classes.get(pending.pop(0))
            if current is None or current.fullname in seen:
                continue
            seen.add(current.fullname)
            wanted = f"{current.fullname}.{name}"
            if wanted in current.methods:
                return wanted
            pending.extend(current.bases)
        return None

    def links(self, function: str) -> tuple[Link, ...]:
        found: list[Link] = [
            Link(kind=LinkKind.DEPEND, target=dependency) for dependency in self.routes.dependencies(function)
        ]
        for call in self._calls.get(function, ()):
            found.extend(self._links_of(call))
        found.extend(self._passes.get(function, ()))
        return tuple(dict.fromkeys(sorted(found, key=lambda link: link.line or 0), None).keys())

    def wires(self, component: str) -> tuple[Wire, ...]:
        return tuple(
            Wire(
                name=param.name,
                type=param.type.text,
                components=tuple(ref for ref in param.type.refs if ref in self._classes),
            )
            for param in self._classes[component].init
        )

    def holders(self, component: str) -> tuple[Holder, ...]:
        """The components built with this one, or with a Protocol it implements."""
        accepted = {component, *self.protocols_of(component)}
        return tuple(
            Holder(component=holder.fullname, wire=wire)
            for holder in self._classes.values()
            for wire in self.wires(holder.fullname)
            if accepted.intersection(wire.components)
        )

    def callers(self, component: str) -> tuple[CallSite, ...]:
        """The call sites outside the component that build it or reach one of its methods, also through a Protocol."""
        accepted = {component, *self.protocols_of(component)}
        return tuple(
            call
            for call in self.scan.calls
            if (call.target == component or call.target.rpartition(".")[0] in accepted)
            and not call.caller.startswith(f"{component}.")
        )

    def _links_of(self, call: CallSite) -> Iterable[Link]:
        if call.kind in (CallKind.VALUE, CallKind.UNRESOLVED):
            return (Link(kind=LinkKind.VALUE, target=call.target, line=call.line),)
        if not self.internal(call.target):
            return (Link(kind=LinkKind.TERMINAL, target=call.target, line=call.line),)
        if call.kind is CallKind.CONSTRUCTOR:
            kind = LinkKind.RAISE if call.raised else LinkKind.CONSTRUCT
            initialiser = self.method(call.target, "__init__")
            return (Link(kind=kind, target=initialiser or call.target, line=call.line, via=call.target),)
        if call.kind is CallKind.PROTOCOL:
            return self._dispatch(call)
        return (Link(kind=LinkKind.CALL, target=call.target, line=call.line),)

    def _dispatch(self, call: CallSite) -> tuple[Link, ...]:
        protocol, _, name = call.target.rpartition(".")
        targets = tuple(
            method for implementation in self.implementations(protocol) if (method := self.method(implementation, name))
        )
        if not targets:
            return (Link(kind=LinkKind.CALL, target=call.target, line=call.line),)
        return tuple(Link(kind=LinkKind.DISPATCH, target=target, line=call.line, via=call.target) for target in targets)


def find(query: str, names: Iterable[str]) -> str:
    """Match a full name, or a unique dotted suffix of one, such as `ConfigurationManagement`."""
    candidates = tuple(names)
    if query in candidates:
        return query
    matches = tuple(name for name in candidates if name.endswith(f".{query}"))
    if not matches:
        raise UnknownNameError(f"nothing is named {query!r}")
    if len(matches) > 1:
        raise AmbiguousNameError(f"{query!r} names {len(matches)} things: " + ", ".join(sorted(matches)))
    return matches[0]
