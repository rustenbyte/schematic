import re
from collections import defaultdict

from pydantic import BaseModel

from schematic.errors import UnknownNameError
from schematic.scan import FROZEN, Argument, CallSite, DecoratorUse, FunctionScan, StaticScan

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
ROUTE_OWNERS = ("fastapi.routing.APIRouter", "fastapi.applications.FastAPI")
ROUTE_DECORATORS = {f"{owner}.{method}": method.upper() for owner in ROUTE_OWNERS for method in HTTP_METHODS} | {
    f"{owner}.api_route": "ANY" for owner in ROUTE_OWNERS
}
DEPENDENCY_MARKERS = frozenset({"fastapi.param_functions.Depends", "fastapi.param_functions.Security"})
PLACEHOLDER = re.compile(r"⟨(\w+)⟩")


class Route(BaseModel):
    model_config = FROZEN

    method: str
    path: str
    handler: str
    operation: str | None
    dependencies: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


class Bound(BaseModel):
    """The value a parameter of a route-building function has for one call of it."""

    model_config = FROZEN

    literal: str | None = None
    ref: str | None = None


type Environment = dict[str, Bound]


class FastApiRoutes:
    """The routes and dependencies of a FastAPI application, read from its decorators and `Depends` markers.

    A route declared inside a function, with a path built from that function's parameters,
    becomes one route per call of the function, with the arguments of that call.
    """

    def __init__(self, *, scan: StaticScan) -> None:
        self._package_prefix = f"{scan.package}."
        self._constructions = {construction.name: construction for construction in scan.constructions}
        self._calls_to: dict[str, list[CallSite]] = defaultdict(list)
        for call in scan.calls:
            self._calls_to[call.target].append(call)
        routes = [
            route
            for function in scan.functions
            for decorator in function.decorators
            if decorator.target in ROUTE_DECORATORS
            for route in self._instances(function, decorator)
        ]
        self._routes = tuple(sorted(dict.fromkeys(routes), key=lambda route: (route.path, route.method)))
        self._by_handler: dict[str, list[Route]] = defaultdict(list)
        for route in self._routes:
            self._by_handler[route.handler].append(route)
        self._dependencies = {
            function.fullname: tuple(name for name in parameter_dependencies(function) if self._internal(name))
            for function in scan.functions
        }

    def all(self) -> tuple[Route, ...]:
        return self._routes

    def find(self, label: str) -> Route:
        method, _, path = label.strip().partition(" ")
        for route in self._routes:
            if route.method == method.upper() and route.path == path.strip():
                return route
        raise UnknownNameError(f"no route {label!r}")

    def of_handler(self, function: str) -> tuple[Route, ...]:
        return tuple(self._by_handler.get(function, ()))

    def dependencies(self, function: str) -> tuple[str, ...]:
        routes = self._by_handler.get(function)
        if routes:
            return tuple(dict.fromkeys(name for route in routes for name in route.dependencies))
        return self._dependencies.get(function, ())

    def _internal(self, name: str) -> bool:
        return name.startswith(self._package_prefix)

    def _instances(self, function: FunctionScan, decorator: DecoratorUse) -> list[Route]:
        factory = function.fullname.rpartition(".<locals>.")[0]
        environments = self._environments(factory, seen=frozenset()) if factory else []
        return [self._route(function, decorator, environment) for environment in environments or [{}]]

    def _environments(self, factory: str, *, seen: frozenset[str]) -> list[Environment]:
        if factory in seen:
            return []
        found: list[Environment] = []
        for call in self._calls_to.get(factory, ()):
            passes_on = any(argument.from_param for argument in call.arguments)
            outers = self._environments(call.caller, seen=seen | {factory}) if passes_on else []
            found.extend(bind(call.arguments, outer) for outer in outers or [{}])
        return found

    def _route(self, function: FunctionScan, decorator: DecoratorUse, environment: Environment) -> Route:
        router_name = decorator.receiver
        if router_name is None and decorator.receiver_param in environment:
            router_name = environment[decorator.receiver_param].ref
        router = self._constructions.get(router_name) if router_name else None
        prefix = router.keywords.get("prefix", "") if router else ""
        path = decorator.args[0] if decorator.args and decorator.args[0] is not None else ""
        path = path or decorator.keywords.get("path", "")
        dependencies = (
            *(router.references if router else ()),
            *(substitute(name, environment) for name in parameter_dependencies(function)),
        )
        return Route(
            method=ROUTE_DECORATORS[decorator.target],
            path=prefix + PLACEHOLDER.sub(lambda match: literal_of(environment, match.group(1)), path),
            handler=function.fullname,
            operation=decorator.keywords.get("operation_id"),
            dependencies=tuple(dict.fromkeys(name for name in dependencies if self._internal(name))),
        )


def bind(arguments: tuple[Argument, ...], outer: Environment) -> Environment:
    environment: Environment = {}
    for argument in arguments:
        if argument.literal is not None or argument.ref is not None:
            environment[argument.param] = Bound(literal=argument.literal, ref=argument.ref)
        elif argument.from_param is not None and argument.from_param in outer:
            environment[argument.param] = outer[argument.from_param]
    return environment


def literal_of(environment: Environment, name: str) -> str:
    bound = environment.get(name)
    return bound.literal if bound is not None and bound.literal is not None else f"⟨{name}⟩"


def substitute(name: str, environment: Environment) -> str:
    match = PLACEHOLDER.fullmatch(name)
    if match is None:
        return name
    bound = environment.get(match.group(1))
    return bound.ref if bound is not None and bound.ref is not None else name


def parameter_dependencies(function: FunctionScan) -> tuple[str, ...]:
    return tuple(
        reference
        for call in function.annotation_calls
        if call.target in DEPENDENCY_MARKERS
        for reference in call.references
    )
