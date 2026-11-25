import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from schematic.board import Board
from schematic.errors import SchematicError
from schematic.routes import FastApiRoutes
from schematic.target import Results, Target
from schematic.text import TextView
from schematic.trace import FlowTrace, StaticTrace, flow_coverage


class Commands:
    """The command line's verbs: each reads its arguments and returns the lines to show."""

    def __init__(self, *, project: Path) -> None:
        self._project = project
        self._results = Results.of(project)

    def scan(self, arguments: argparse.Namespace) -> list[str]:
        target = Target.at(self._project, package=arguments.package)
        scan = target.scan(output=self._results.scan_file)
        board = Board(scan=scan, routes=FastApiRoutes(scan=scan))
        return [*TextView(board=board).summary(), f"saved      {self._results.scan_file}"]

    def routes(self, arguments: argparse.Namespace) -> list[str]:
        return TextView(board=self._board()).routes()

    def trace(self, arguments: argparse.Namespace) -> list[str]:
        board = self._board()
        start = str(arguments.start)
        root = board.routes.find(start).handler if " " in start.strip() else board.function(start).fullname
        return TextView(board=board).tree(StaticTrace(board=board).tree(root))

    def chip(self, arguments: argparse.Namespace) -> list[str]:
        return TextView(board=self._board()).chip(str(arguments.component))

    def record(self, arguments: argparse.Namespace) -> list[str]:
        board = self._board()
        target = Target.at(self._project, package=board.package)
        pytest_args = tuple(arguments.pytest_args)
        recording = target.record(
            pytest_args[1:] if pytest_args[:1] == ("--",) else pytest_args, output=self._results.recording_file
        )
        return [*TextView(board=board).flows(recording), f"saved {self._results.recording_file}"]

    def flows(self, arguments: argparse.Namespace) -> list[str]:
        return TextView(board=self._board()).flows(self._results.recording())

    def flow(self, arguments: argparse.Namespace) -> list[str]:
        board = self._board()
        view = TextView(board=board)
        lines: list[str] = []
        for flow in self._results.recording().flow(str(arguments.name)):
            lines.extend(view.flow(flow, FlowTrace(flow=flow).roots(), flow_coverage(board, flow)))
        if not lines:
            raise SchematicError(f"no recorded flow is named {arguments.name!r}")
        return lines

    def _board(self) -> Board:
        scan = self._results.scan()
        return Board(scan=scan, routes=FastApiRoutes(scan=scan))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="schematic", description="Map the architecture of a Python application.")
    root.add_argument("--project", type=Path, default=Path.cwd(), help="root of the project (default: here)")
    verbs = root.add_subparsers(dest="verb", required=True)
    scan = verbs.add_parser("scan", help="resolve every call of the package with the project's own mypy")
    scan.add_argument("--package", help="the package to scan (default: the project's name)")
    verbs.add_parser("routes", help="list the HTTP routes and their handlers")
    trace = verbs.add_parser("trace", help="every path a route or a function may take")
    trace.add_argument("start", help='a route such as "GET /api/cm/components", or a function name')
    chip = verbs.add_parser("chip", help="one component: its wires, pins, holders and callers")
    chip.add_argument("component", help="a class name, or a unique end of one")
    record = verbs.add_parser("record", help="run tests and record the path each one takes")
    record.add_argument("pytest_args", nargs=argparse.REMAINDER, help="arguments for pytest, after --")
    verbs.add_parser("flows", help="list the recorded flows")
    flow = verbs.add_parser("flow", help="the path one recorded flow took, against the paths it may take")
    flow.add_argument("name", help="the flow's name, or its test id")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    commands = Commands(project=arguments.project)
    verbs: dict[str, Callable[[argparse.Namespace], list[str]]] = {
        "scan": commands.scan,
        "routes": commands.routes,
        "trace": commands.trace,
        "chip": commands.chip,
        "record": commands.record,
        "flows": commands.flows,
        "flow": commands.flow,
    }
    try:
        lines = verbs[arguments.verb](arguments)
    except SchematicError as error:
        sys.stderr.write(f"schematic: {error}\n")
        return 2
    sys.stdout.write("\n".join(lines) + "\n")
    return 0
