from schematic.scan import Argument, CallKind, StaticScan


def calls_from(scan: StaticScan, caller: str) -> set[tuple[CallKind, str]]:
    return {(call.kind, call.target) for call in scan.calls if call.caller == caller}


def test_a_method_call_resolves_to_the_method(scan: StaticScan) -> None:
    assert (CallKind.FUNCTION, "shop.store.MemoryOrders._count") in calls_from(scan, "shop.store.MemoryOrders.place")


def test_a_call_through_a_protocol_is_marked(scan: StaticScan) -> None:
    assert calls_from(scan, "shop.app.place") == {(CallKind.PROTOCOL, "shop.ports.Orders.place")}


def test_a_raised_construction_is_marked(scan: StaticScan) -> None:
    raised = {call.target for call in scan.calls if call.caller == "shop.store.MemoryOrders.place" and call.raised}
    assert raised == {"shop.errors.EmptyItemError"}


def test_a_nested_function_is_named_as_python_names_it(scan: StaticScan) -> None:
    assert "shop.app.add_health_routes.<locals>.health" in {function.fullname for function in scan.functions}


def test_a_call_keeps_the_strings_and_names_it_passes(scan: StaticScan) -> None:
    (call,) = (call for call in scan.calls if call.target == "shop.app.add_health_routes")
    assert call.arguments == (
        Argument(param="router", literal=None, ref="shop.app.router", from_param=None),
        Argument(param="path", literal="/status", ref=None, from_param=None),
    )


def test_a_protocol_is_bound_where_a_concrete_class_is_given_for_it(scan: StaticScan) -> None:
    bindings = {(binding.protocol, binding.implementation, binding.site) for binding in scan.bindings}
    assert {
        ("shop.ports.Orders", "shop.store.MemoryOrders", "shop.app.orders"),
        ("shop.ports.Clock", "shop.store.SystemClock", "shop.app.orders"),
    } <= bindings


def test_docstrings_are_kept_for_modules_classes_and_functions(scan: StaticScan) -> None:
    assert next(module.doc for module in scan.modules if module.name == "shop.errors") == "What the shop refuses to do."
    assert next(c.doc for c in scan.classes if c.fullname == "shop.errors.ShopError") == (
        "A refusal, with the reason in its message."
    )
    functions = {function.fullname: function.doc for function in scan.functions}
    assert functions["shop.store.MemoryOrders._count"] == "Count every order placed so far."
    assert functions["shop.store.MemoryOrders.place"] is None
