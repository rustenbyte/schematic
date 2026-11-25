"""Run tests of the target and record, per test, which functions of its package call which.

Runs inside the target's interpreter, so it imports only the standard library and
pytest: `python -I -B flow_recorder.py <package dir> <output file> [pytest args...]`.
A test marked `@pytest.mark.flow("<use case>")` records under that name, any other
test under its node id. Only the call phase of a test is recorded, not its fixtures.
"""

import json
import os
import sys
import time
from collections.abc import Generator
from importlib.util import find_spec
from pathlib import Path
from types import CodeType, FrameType

import pytest

MONITORING = sys.monitoring
TOOL = MONITORING.PROFILER_ID


class Flow:
    def __init__(self, *, name: str, test: str) -> None:
        self.name = name
        self.test = test
        self.outcome = "unknown"
        self.started = time.perf_counter()
        self.seconds = 0.0
        self.edges: dict[tuple[str | None, str, str | None], list[int]] = {}
        self.raises: dict[tuple[str, str, bool], int] = {}
        self.handled: dict[tuple[str, str], int] = {}

    def edge(self, caller: str | None, callee: str, via: str | None) -> None:
        found = self.edges.get((caller, callee, via))
        if found is None:
            self.edges[(caller, callee, via)] = [1, len(self.edges)]
            return
        found[0] += 1

    def raised(self, function: str, exception: str, *, origin: bool) -> None:
        key = (function, exception, origin)
        self.raises[key] = self.raises.get(key, 0) + 1

    def caught(self, function: str, exception: str) -> None:
        key = (function, exception)
        self.handled[key] = self.handled.get(key, 0) + 1

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "test": self.test,
            "outcome": self.outcome,
            "seconds": round(self.seconds, 4),
            "edges": [
                {"caller": caller, "callee": callee, "via": via, "count": count, "order": order}
                for (caller, callee, via), (count, order) in self.edges.items()
            ],
            "raises": [
                {"function": function, "exception": exception, "origin": origin, "count": count}
                for (function, exception, origin), count in self.raises.items()
            ],
            "handled": [
                {"function": function, "exception": exception, "count": count}
                for (function, exception), count in self.handled.items()
            ],
        }


def is_inline(code: CodeType) -> bool:
    return code.co_name.startswith("<") and code.co_name != "<module>"


def enclosing_function(qualname: str) -> list[str]:
    """Count a generator expression or a lambda as part of the function that holds it."""
    parts = qualname.split(".")
    while parts and parts[-1].startswith("<") and parts[-1] != "<module>":
        parts.pop()
    return parts


def exception_name(exception: BaseException) -> str:
    kind = type(exception)
    return f"{kind.__module__}.{kind.__qualname__}"


class Recorder:
    def __init__(self, *, package_dir: Path) -> None:
        self._package_dir = package_dir
        self._prefix = f"{package_dir}{os.sep}"
        self._names: dict[CodeType, str | None] = {}
        self.current: Flow | None = None
        self.flows: list[Flow] = []

    def start(self) -> None:
        MONITORING.use_tool_id(TOOL, "schematic")
        MONITORING.register_callback(TOOL, MONITORING.events.PY_START, self._on_start)
        MONITORING.register_callback(TOOL, MONITORING.events.RAISE, self._on_raise)
        MONITORING.register_callback(TOOL, MONITORING.events.EXCEPTION_HANDLED, self._on_handled)
        MONITORING.set_events(
            TOOL, MONITORING.events.PY_START | MONITORING.events.RAISE | MONITORING.events.EXCEPTION_HANDLED
        )

    def stop(self) -> None:
        MONITORING.set_events(TOOL, 0)
        MONITORING.free_tool_id(TOOL)

    def name_of(self, code: CodeType) -> str | None:
        if code in self._names:
            return self._names[code]
        name = None
        if code.co_filename.startswith(self._prefix):
            parts = Path(code.co_filename).relative_to(self._package_dir.parent).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            name = ".".join([module, *enclosing_function(code.co_qualname)])
        self._names[code] = name
        return name

    def _on_start(self, code: CodeType, offset: int) -> object:
        callee = self.name_of(code)
        if callee is None:
            return MONITORING.DISABLE
        flow = self.current
        if flow is None or is_inline(code):
            return None
        caller, via = self._caller(sys._getframe(1).f_back)
        flow.edge(caller, callee, via)
        return None

    def _caller(self, frame: FrameType | None) -> tuple[str | None, str | None]:
        via = None
        while frame is not None:
            name = self.name_of(frame.f_code)
            if name is not None:
                return name, via
            if via is None:
                via = str(frame.f_globals.get("__name__", ""))
            frame = frame.f_back
        return None, via

    def _on_raise(self, code: CodeType, offset: int, exception: BaseException) -> object:
        function = self.name_of(code)
        flow = self.current
        if function is not None and flow is not None:
            trace = exception.__traceback__
            flow.raised(function, exception_name(exception), origin=trace is None or trace.tb_next is None)
        return None

    def _on_handled(self, code: CodeType, offset: int, exception: BaseException) -> object:
        function = self.name_of(code)
        flow = self.current
        if function is not None and flow is not None:
            flow.caught(function, exception_name(exception))
        return None


class FlowPlugin:
    def __init__(self, *, recorder: Recorder) -> None:
        self._recorder = recorder
        self._by_test: dict[str, Flow] = {}

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, object, object]:
        marker = item.get_closest_marker("flow")
        name = str(marker.args[0]) if marker is not None and marker.args else item.nodeid
        flow = Flow(name=name, test=item.nodeid)
        self._by_test[item.nodeid] = flow
        self._recorder.flows.append(flow)
        self._recorder.current = flow
        try:
            return (yield)
        finally:
            self._recorder.current = None
            flow.seconds = time.perf_counter() - flow.started

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        flow = self._by_test.get(report.nodeid)
        if flow is not None and report.when == "call":
            flow.outcome = report.outcome


def quiet_options() -> list[str]:
    """Keep the run from writing into the target: no pytest cache, and no coverage data file."""
    options = ["-p", "no:cacheprovider"]
    if find_spec("pytest_cov") is not None:
        options.append("--no-cov")
    return options


def main() -> None:
    package_dir, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    recorder = Recorder(package_dir=package_dir)
    recorder.start()
    try:
        exit_code = pytest.main([*quiet_options(), *sys.argv[3:]], plugins=[FlowPlugin(recorder=recorder)])
    finally:
        recorder.stop()
    output.write_text(
        json.dumps(
            {
                "package": package_dir.name,
                "pytest_exit_code": int(exit_code),
                "flows": [flow.as_json() for flow in recorder.flows],
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
