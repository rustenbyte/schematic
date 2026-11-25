import pytest

from schematic.board import Board
from schematic.errors import UnknownNameError
from schematic.routes import Route


def test_routes_join_the_router_prefix_and_expand_a_route_building_function(board: Board) -> None:
    assert board.routes.all() == (
        Route(
            method="GET",
            path="/orders/status/health",
            handler="shop.app.add_health_routes.<locals>.health",
            operation=None,
            dependencies=("shop.app.caller_name",),
        ),
        Route(
            method="POST",
            path="/orders/{item}",
            handler="shop.app.place",
            operation="placeOrder",
            dependencies=("shop.app.caller_name", "shop.app.orders"),
        ),
    )


def test_a_route_is_found_by_its_method_and_path(board: Board) -> None:
    assert board.routes.find("post /orders/{item}").handler == "shop.app.place"


def test_an_unknown_route_is_refused(board: Board) -> None:
    with pytest.raises(UnknownNameError, match="no route 'GET /baskets'"):
        board.routes.find("GET /baskets")
