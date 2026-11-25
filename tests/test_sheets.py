import pytest

from schematic.board import Board
from schematic.errors import UnknownNameError
from schematic.recording import Recording
from schematic.scan import ClassKind, ParamKind, StaticScan
from schematic.sheets import (
    Call,
    Collaborator,
    DataType,
    Field,
    FunctionRef,
    Inbound,
    Member,
    Outbound,
    Parameter,
    Part,
    PartDetail,
    PartKind,
    Role,
    Sheets,
    SheetSummary,
    Signature,
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
        SheetSummary(id="errors", title="errors", parts=2),
        SheetSummary(id="ports", title="ports", parts=0),
        SheetSummary(id="store", title="store", parts=2),
    )


def test_a_flow_is_numbered_against_the_parts_of_the_sheet(sheets: Sheets) -> None:
    flows = sheets.flows("store")
    first = next(path for path in flows.flows if path.name == "place an order")
    assert first.edges == ((-1, 0, 1), (-1, 1, 1), (1, 2, 1))
    assert [flows.functions[i] for i in (0, 1, 2)] == [
        FunctionRef(sheet="store", part="shop.store.MemoryOrders", method="new"),
        FunctionRef(sheet="store", part="shop.store.MemoryOrders", method="place"),
        FunctionRef(sheet="store", part="shop.store.MemoryOrders", method="_count"),
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
    assert sheets.detail("shop.store.MemoryOrders") == PartDetail(
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
    members = sheets.detail("shop.app").members
    assert [member.name for member in members] == ["orders", "caller_name", "place", "add_health_routes"]
    assert members[3].doc == "Add a health route under `path`."


def test_an_unknown_part_is_refused(sheets: Sheets) -> None:
    with pytest.raises(UnknownNameError, match=r"no part 'shop\.store\.Basket'"):
        sheets.detail("shop.store.Basket")


def test_a_call_sends_its_parameters_and_returns_its_result_with_the_data_classes_in_them(sheets: Sheets) -> None:
    data = sheets.data(["shop.errors|refusal"])
    assert data.signatures == (
        Signature(
            function="shop.errors.refusal",
            parameters=(
                Parameter(name="item", kind=ParamKind.POSITIONAL, type="str", types=()),
                Parameter(name="reason", kind=ParamKind.POSITIONAL, type="str", types=()),
            ),
            returns="shop.errors.Refusal",
            return_types=("shop.errors.Refusal",),
            doc=None,
        ),
    )
    assert data.types == (
        DataType(
            id="shop.errors.Refusal",
            title="Refusal",
            kinds=(ClassKind.DATACLASS,),
            doc="What a refused order was, and why.",
            data=True,
            fields=(Field(name="item", type="str"), Field(name="reason", type="str")),
        ),
    )


def test_a_construction_sends_the_constructor_parameters_and_returns_the_part(sheets: Sheets) -> None:
    data = sheets.data(["shop.store.MemoryOrders|new"])
    clock = Parameter(name="clock", kind=ParamKind.KEYWORD, type="shop.ports.Clock", types=("shop.ports.Clock",))
    assert (data.signatures[0].parameters, data.signatures[0].returns) == ((clock,), "shop.store.MemoryOrders")
    assert [(found.id, found.data, [f.name for f in found.fields]) for found in data.types] == [
        ("shop.ports.Clock", False, []),
        ("shop.store.MemoryOrders", False, ["clock"]),
    ]


def test_a_construction_of_a_dataclass_takes_its_generated_constructor(sheets: Sheets) -> None:
    parameters = sheets.data(["shop.errors.Refusal|new"]).signatures[0].parameters
    assert [(p.name, p.type) for p in parameters] == [("item", "str"), ("reason", "str")]


def test_data_for_an_unknown_function_is_refused(sheets: Sheets) -> None:
    with pytest.raises(UnknownNameError, match=r"no method 'fill' on 'shop\.store\.MemoryOrders'"):
        sheets.data(["shop.store.MemoryOrders|fill"])
