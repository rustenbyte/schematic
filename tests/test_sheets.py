import pytest

from schematic.board import Board
from schematic.errors import UnknownNameError
from schematic.recording import Recording
from schematic.scan import StaticScan
from schematic.sheets import (
    Call,
    Collaborator,
    FunctionRef,
    Inbound,
    Member,
    Outbound,
    Part,
    PartDetail,
    PartKind,
    Role,
    Sheets,
    SheetSummary,
)


@pytest.fixture
def sheets(board: Board, recording: Recording) -> Sheets:
    return Sheets(board=board, recording=recording)


def store_path(scan: StaticScan) -> str:
    return next(module.path for module in scan.modules if module.name == "shop.store")


def test_the_overview_has_a_part_per_top_level_module_and_a_wire_per_pair(sheets: Sheets) -> None:
    overview = sheets.sheet("shop")
    assert [part.id for part in overview.parts] == ["app", "errors", "ports", "store"]
    assert overview.calls == (Call(source=0, source_method="", target=3, target_method="", count=3, lines=()),)
    assert {call.target for call in overview.outbound} == {"library:fastapi"}


def test_a_sheet_has_its_parts_their_calls_and_the_calls_from_other_sheets(sheets: Sheets, scan: StaticScan) -> None:
    store = sheets.sheet("store")
    assert store.parts == (
        Part(
            id="shop.store.MemoryOrders",
            title="MemoryOrders",
            kind=PartKind.CLASS,
            role=Role.SERVICE,
            file="store.py",
            path=store_path(scan),
            service="MemoryOrders",
            methods=("new", "place"),
        ),
        Part(
            id="shop.store.SystemClock",
            title="SystemClock",
            kind=PartKind.CLASS,
            role=Role.SERVICE,
            file="store.py",
            path=store_path(scan),
            service="SystemClock",
            methods=("now", "new"),
        ),
    )
    assert store.calls == (Call(source=0, source_method="place", target=1, target_method="now", count=1, lines=(20,)),)
    assert store.inbound == (
        Inbound(sheet="app", target=0, target_method="new", count=1),
        Inbound(sheet="app", target=0, target_method="place", count=1),
        Inbound(sheet="app", target=1, target_method="new", count=1),
    )


def test_a_module_of_functions_is_one_part_that_calls_other_sheets_and_libraries(sheets: Sheets) -> None:
    app = sheets.sheet("app")
    assert [part.id for part in app.parts] == ["shop.app"]
    assert Outbound(source=0, source_method="orders", target="sheet:store", count=2) in app.outbound
    assert {call.target for call in app.outbound} == {"sheet:store", "library:fastapi"}


def test_the_index_lists_the_overview_first(sheets: Sheets) -> None:
    assert sheets.index().sheets == (
        SheetSummary(id="shop", title="shop", parts=4),
        SheetSummary(id="app", title="app", parts=1),
        SheetSummary(id="errors", title="errors", parts=0),
        SheetSummary(id="ports", title="ports", parts=0),
        SheetSummary(id="store", title="store", parts=2),
    )


def test_a_flow_is_numbered_against_the_parts_of_the_sheet(sheets: Sheets) -> None:
    flows = sheets.flows("store")
    first = next(path for path in flows.flows if path.name == "place an order")
    assert first.edges == ((-1, 0, 1), (-1, 1, 1), (1, 2, 1))
    assert [flows.functions[i] for i in (0, 1, 2)] == [
        FunctionRef(sheet="store", part=0, method="new"),
        FunctionRef(sheet="store", part=0, method="place"),
        FunctionRef(sheet="store", part=0, method="_count"),
    ]


def test_a_sheet_lists_only_the_flows_that_run_on_it(sheets: Sheets) -> None:
    assert [path.name for path in sheets.flows("store").flows] == [
        "place an order",
        "place an empty order",
        "place an order through the route",
        "tests/test_orders.py::test_without_a_flow_name",
    ]
    assert sheets.flows("ports").flows == ()


def test_without_a_recording_a_sheet_has_no_flows(board: Board) -> None:
    assert Sheets(board=board, recording=None).flows("store").flows == ()


def test_an_unknown_sheet_is_refused(sheets: Sheets) -> None:
    with pytest.raises(UnknownNameError, match="no sheet 'basket'"):
        sheets.sheet("basket")


def test_a_class_has_its_place_wiring_members_docstrings_and_raises(sheets: Sheets, scan: StaticScan) -> None:
    assert sheets.part("store", 0) == PartDetail(
        id="shop.store.MemoryOrders",
        title="MemoryOrders",
        kind=PartKind.CLASS,
        role=Role.SERVICE,
        path=store_path(scan),
        line=12,
        doc=None,
        kinds=(),
        implements=("shop.ports.Orders",),
        built_with=(Collaborator(name="clock", type="shop.ports.Clock", parts=("shop.ports.Clock",)),),
        held_by=(),
        members=(
            Member(name="new", line=13, doc=None),
            Member(name="place", line=17, doc=None),
            Member(name="_count", line=23, doc="Count every order placed so far."),
        ),
        raises=("shop.errors.EmptyItemError",),
        types=(),
    )


def test_a_module_lists_its_own_functions_but_not_the_ones_nested_in_them(sheets: Sheets) -> None:
    members = sheets.part("app", 0).members
    assert [member.name for member in members] == ["orders", "caller_name", "place", "add_health_routes"]
    assert members[3].doc == "Add a health route under `path`."


def test_a_part_outside_the_sheet_is_refused(sheets: Sheets) -> None:
    with pytest.raises(UnknownNameError, match="no part 2 on sheet 'store'"):
        sheets.part("store", 2)
