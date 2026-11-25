from schematic.board import Board
from schematic.text import TextView
from schematic.trace import StaticTrace


def test_a_static_trace_shows_every_path_a_handler_may_take(board: Board) -> None:
    tree = StaticTrace(board=board).tree("shop.app.place")
    assert TextView(board=board).tree(tree) == [
        "app.place",
        "├─ ◇ app.caller_name",
        "├─ ◇ app.orders",
        "│  ├─ new store.MemoryOrders",
        "│  └─ new store.SystemClock",
        "└─ store.MemoryOrders.place  ⇐ ports.Orders",
        "   ├─ raises errors.EmptyItemError",
        "   ├─ store.SystemClock.now  ⇐ ports.Clock",
        "   │  └─ ⏚ datetime: datetime.now",
        "   └─ store.MemoryOrders._count",
    ]


def test_the_reach_of_a_handler_holds_every_function_it_may_run(board: Board) -> None:
    assert StaticTrace(board=board).reach("shop.app.place") == {
        "shop.app.place",
        "shop.app.caller_name",
        "shop.app.orders",
        "shop.store.MemoryOrders.__init__",
        "shop.store.MemoryOrders.place",
        "shop.store.MemoryOrders._count",
        "shop.store.SystemClock.now",
        "shop.errors.ShopError.__init__",
    }
