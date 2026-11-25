import pytest

from schematic.board import Board, Holder, Link, LinkKind, Wire
from schematic.errors import AmbiguousNameError, UnknownNameError


def test_a_protocol_has_the_implementations_wired_to_it(board: Board) -> None:
    assert board.implementations("shop.ports.Orders") == ("shop.store.MemoryOrders",)
    assert board.implementations("shop.ports.Clock") == ("shop.store.SystemClock",)


def test_a_handler_links_to_its_dependencies_and_through_the_protocol_to_the_implementation(board: Board) -> None:
    assert board.links("shop.app.place") == (
        Link(kind=LinkKind.DEPEND, target="shop.app.caller_name"),
        Link(kind=LinkKind.DEPEND, target="shop.app.orders"),
        Link(
            kind=LinkKind.DISPATCH,
            target="shop.store.MemoryOrders.place",
            line=24,
            via="shop.ports.Orders.place",
        ),
    )


def test_a_raise_links_to_the_initialiser_the_error_inherits(board: Board) -> None:
    raises = [link for link in board.links("shop.store.MemoryOrders.place") if link.kind is LinkKind.RAISE]
    assert raises == [
        Link(kind=LinkKind.RAISE, target="shop.errors.ShopError.__init__", line=19, via="shop.errors.EmptyItemError")
    ]


def test_a_component_is_wired_through_its_constructor(board: Board) -> None:
    assert board.wires("shop.store.MemoryOrders") == (
        Wire(name="clock", type="shop.ports.Clock", components=("shop.ports.Clock",)),
    )


def test_a_component_is_held_where_its_protocol_is_wired(board: Board) -> None:
    assert board.holders("shop.store.SystemClock") == (
        Holder(
            component="shop.store.MemoryOrders",
            wire=Wire(name="clock", type="shop.ports.Clock", components=("shop.ports.Clock",)),
        ),
    )


def test_a_component_is_found_by_the_end_of_its_name(board: Board) -> None:
    assert board.component("MemoryOrders").fullname == "shop.store.MemoryOrders"


def test_an_unknown_name_is_refused(board: Board) -> None:
    with pytest.raises(UnknownNameError, match="nothing is named 'Basket'"):
        board.component("Basket")


def test_a_name_that_fits_two_functions_is_refused(board: Board) -> None:
    with pytest.raises(AmbiguousNameError, match="'place' names 3 things"):
        board.function("place")
