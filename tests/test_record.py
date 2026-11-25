import pytest

from schematic.board import Board
from schematic.errors import ProbeFailedError
from schematic.recording import RaiseStep, Recording
from schematic.target import Target
from schematic.trace import flow_coverage


def test_each_test_is_a_flow_named_by_its_marker_or_its_id(recording: Recording) -> None:
    assert [(flow.name, flow.outcome) for flow in recording.flows] == [
        ("place an order", "passed"),
        ("place an empty order", "passed"),
        ("place an order through the route", "passed"),
        ("tests/test_orders.py::test_without_a_flow_name", "passed"),
    ]


def test_a_flow_records_who_called_whom_in_order(recording: Recording) -> None:
    (flow,) = recording.flow("place an order")
    assert [(edge.caller, edge.callee) for edge in flow.ordered_edges()] == [
        (None, "shop.store.MemoryOrders.__init__"),
        (None, "shop.store.MemoryOrders.place"),
        ("shop.store.MemoryOrders.place", "shop.store.MemoryOrders._count"),
    ]


def test_a_flow_records_where_an_exception_was_raised(recording: Recording) -> None:
    (flow,) = recording.flow("place an empty order")
    assert flow.raises == (
        RaiseStep(
            function="shop.store.MemoryOrders.place", exception="shop.errors.EmptyItemError", origin=True, count=1
        ),
    )


def test_a_flow_through_a_handler_shows_what_it_left_untaken(board: Board, recording: Recording) -> None:
    (flow,) = recording.flow("place an order through the route")
    coverage = flow_coverage(board, flow)
    assert coverage.handlers == ("shop.app.place",)
    assert coverage.missed == {"shop.app.caller_name", "shop.errors.ShopError.__init__"}


def test_a_probe_that_fails_is_reported(broken_target: Target) -> None:
    with pytest.raises(ProbeFailedError, match=r"static_scan\.py stopped with exit code"):
        broken_target.scan(output=broken_target.root / "broken" / "scan.json")
