import sys
from pathlib import Path

import pytest

from schematic.board import Board
from schematic.recording import Recording
from schematic.routes import FastApiRoutes
from schematic.scan import StaticScan
from schematic.target import Results, Target

SAMPLE = {
    "pyproject.toml": """
[project]
name = "shop"
version = "0"

[tool.pytest.ini_options]
pythonpath = ["src"]
markers = ["flow(name): the use case a test plays"]
""",
    "src/shop/__init__.py": "",
    "src/shop/ports.py": """
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class Orders(Protocol):
    def place(self, item: str) -> int: ...
""",
    "src/shop/errors.py": """
\"\"\"What the shop refuses to do.\"\"\"


class ShopError(Exception):
    \"\"\"A refusal, with the reason in its message.\"\"\"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EmptyItemError(ShopError):
    pass
""",
    "src/shop/store.py": """
from datetime import UTC, datetime

from shop.errors import EmptyItemError
from shop.ports import Clock


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class MemoryOrders:
    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._placed: list[tuple[str, datetime]] = []

    def place(self, item: str) -> int:
        if not item:
            raise EmptyItemError("an order needs an item")
        self._placed.append((item, self._clock.now()))
        return self._count()

    def _count(self) -> int:
        \"\"\"Count every order placed so far.\"\"\"
        return len(self._placed)
""",
    "src/shop/app.py": """
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI

from shop.ports import Orders
from shop.store import MemoryOrders, SystemClock


def orders() -> Orders:
    return MemoryOrders(clock=SystemClock())


def caller_name() -> str:
    return "guest"


OrdersDep = Annotated[Orders, Depends(orders)]

router = APIRouter(prefix="/orders", dependencies=[Depends(caller_name)])


@router.post("/{item}", operation_id="placeOrder")
def place(item: str, service: OrdersDep) -> int:
    return service.place(item)


def add_health_routes(router: APIRouter, *, path: str) -> None:
    \"\"\"Add a health route under `path`.\"\"\"

    @router.get(f"{path}/health")
    def health() -> str:
        return "ok"


add_health_routes(router, path="/status")

app = FastAPI()
app.include_router(router)
""",
    "tests/test_orders.py": """
from datetime import UTC, datetime

import pytest

from shop.app import orders, place
from shop.errors import EmptyItemError
from shop.store import MemoryOrders


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.flow("place an order")
def test_an_order_is_placed() -> None:
    assert MemoryOrders(clock=FixedClock()).place("tea") == 1


@pytest.mark.flow("place an empty order")
def test_an_empty_order_is_refused() -> None:
    with pytest.raises(EmptyItemError):
        MemoryOrders(clock=FixedClock()).place("")


@pytest.mark.flow("place an order through the route")
def test_the_route_places_an_order() -> None:
    assert place("tea", orders()) == 1


def test_without_a_flow_name() -> None:
    assert MemoryOrders(clock=FixedClock()).place("coffee") == 1
""",
}


@pytest.fixture(scope="session")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sample")
    for name, text in SAMPLE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.lstrip(), encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def target(sample: Path) -> Target:
    return Target(root=sample, package_dir=sample / "src" / "shop", interpreter=Path(sys.executable))


@pytest.fixture(scope="session")
def scan(target: Target, tmp_path_factory: pytest.TempPathFactory) -> StaticScan:
    return target.scan(output=tmp_path_factory.mktemp("results") / "scan.json")


@pytest.fixture(scope="session")
def recording(target: Target, tmp_path_factory: pytest.TempPathFactory) -> Recording:
    return target.record(("-q", "tests"), output=tmp_path_factory.mktemp("results") / "recording.json")


@pytest.fixture(scope="session")
def board(scan: StaticScan) -> Board:
    return Board(scan=scan, routes=FastApiRoutes(scan=scan))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def results(home: Path, sample: Path, scan: StaticScan, recording: Recording) -> Results:
    kept = Results.of(sample)
    kept.directory.mkdir(parents=True)
    kept.scan_file.write_text(scan.model_dump_json(), encoding="utf-8")
    kept.recording_file.write_text(recording.model_dump_json(), encoding="utf-8")
    return kept


@pytest.fixture
def broken_target(sample: Path) -> Target:
    return Target(root=sample, package_dir=sample / "src" / "missing", interpreter=Path(sys.executable))
