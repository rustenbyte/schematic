from pathlib import Path

import pytest

from schematic.cli import main
from schematic.target import Results


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, list[str], str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def test_routes_lists_each_route_with_its_handler(
    results: Results, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "--project", str(sample), "routes") == (
        0,
        [
            "GET /orders/status/health  app.add_health_routes.<locals>.health",
            "POST /orders/{item}        app.place",
        ],
        "",
    )


def test_chip_shows_wires_pins_holders_and_callers(
    results: Results, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "--project", str(sample), "chip", "MemoryOrders")[1] == [
        "store.MemoryOrders  shop.store:12",
        "implements   ports.Orders",
        "wire         clock: ports.Clock  → ports.Clock",
        "pins         place",
        "called from  app  ×2",
    ]


def test_flow_shows_the_path_taken_against_the_paths_possible(
    results: Results, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines, _ = run(capsys, "--project", str(sample), "flow", "place an order through the route")
    assert code == 0
    assert lines[2:] == [
        "app.orders  ← test_orders",
        "└─ store.MemoryOrders.__init__",
        "app.place  ← test_orders",
        "└─ store.MemoryOrders.place",
        "   ├─ store.SystemClock.now",
        "   └─ store.MemoryOrders._count",
        "",
        "static reach of app.place: 8 functions, 6 taken, 2 not taken",
        "  not taken  app: caller_name",
        "  not taken  errors.ShopError: __init__",
    ]


def test_a_command_without_a_scan_says_what_to_run(
    home: Path, sample: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "--project", str(sample), "routes") == (
        2,
        [],
        "schematic: no scan yet: run `schematic scan` first\n",
    )
