from pydantic import BaseModel

from schematic.scan import FROZEN


class FlowEdge(BaseModel):
    """One caller-to-callee step of a flow; a caller of None entered from outside the package."""

    model_config = FROZEN

    caller: str | None
    callee: str
    via: str | None
    count: int
    order: int


class RaiseStep(BaseModel):
    """An exception passing through a function; origin marks the function that raised it."""

    model_config = FROZEN

    function: str
    exception: str
    origin: bool
    count: int


class Handling(BaseModel):
    model_config = FROZEN

    function: str
    exception: str
    count: int


class Flow(BaseModel):
    model_config = FROZEN

    name: str
    test: str
    outcome: str
    seconds: float
    edges: tuple[FlowEdge, ...]
    raises: tuple[RaiseStep, ...]
    handled: tuple[Handling, ...]

    def functions(self) -> frozenset[str]:
        return frozenset(edge.callee for edge in self.edges)

    def ordered_edges(self) -> tuple[FlowEdge, ...]:
        return tuple(sorted(self.edges, key=lambda edge: edge.order))


class Recording(BaseModel):
    model_config = FROZEN

    package: str
    pytest_exit_code: int
    flows: tuple[Flow, ...]

    def flow(self, name: str) -> tuple[Flow, ...]:
        return tuple(flow for flow in self.flows if name in (flow.name, flow.test))
