import assert from "node:assert/strict";
import { test } from "node:test";
import type { FlowPath, FunctionRef, Sheet, SheetComparison } from "../src/api.ts";
import { OTHER, ROWS_TOP, ROW, at, combine, drawing, elkGraph } from "../src/model.ts";
import type { Drawing, ViewState } from "../src/model.ts";

const SHEET: Sheet = {
  id: "store",
  title: "store",
  parts: [
    { id: "shop.store.Orders", title: "Orders", kind: "class", role: "service", file: "store.py", path: "/shop/store.py", service: "Orders", methods: ["run"] },
    { id: "shop.store.Ledger", title: "Ledger", kind: "class", role: "internal", file: "store.py", path: "/shop/store.py", service: "Orders", methods: ["read", "write"] },
    { id: "shop.store.rules", title: "rules", kind: "module", role: "module", file: "rules.py", path: "/shop/rules.py", service: "Orders", methods: ["helper"] },
  ],
  calls: [
    { source: 0, source_method: "run", target: 1, target_method: "read", count: 2, lines: [4, 6] },
    { source: 0, source_method: "run", target: 1, target_method: "write", count: 1, lines: [7] },
    { source: 1, source_method: "read", target: 2, target_method: "helper", count: 1, lines: [12] },
  ],
  inbound: [{ sheet: "app", target: 0, target_method: "run", count: 1 }],
  outbound: [{ source: 1, source_method: "write", target: "library:pyoxigraph", count: 1 }],
};
const FUNCTIONS: FunctionRef[] = [
  { sheet: "store", part: "shop.store.Orders", method: "run" },
  { sheet: "store", part: "shop.store.Ledger", method: "read" },
  { sheet: "store", part: "shop.store.rules", method: "helper" },
];
const FLOW: FlowPath = {
  test: "tests/test_store.py::test_an_order_reads_the_ledger",
  name: "tests/test_store.py::test_an_order_reads_the_ledger",
  outcome: "passed",
  edges: [[-1, 0, 1], [0, 1, 1], [1, 2, 1]],
  raises: [],
};

function state(changes: Partial<ViewState> = {}): ViewState {
  return { grouping: "none", expanded: new Set<number>(), flow: null, ...changes };
}

function wires(d: Drawing): string[] {
  return d.wires.map((w) => `${w.sourcePort} > ${w.targetPort}${w.lit ? " lit" : ""}`);
}

test("collapsed parts have one wire per pair, with a port per neighbour", () => {
  const d = drawing(SHEET, state(), FUNCTIONS);
  assert.deepEqual(wires(d), ["0|e|c1 > 1|w|c0", "1|e|c2 > 2|w|c1"]);
  assert.deepEqual(d.rows, [null, null, null]);
});

test("calls from and to other sheets are flags on their own ports", () => {
  const d = drawing(SHEET, state(), FUNCTIONS);
  assert.deepEqual([...(d.sides[0]?.west.get("0|w|p:app")?.flags ?? [])], [["app", { count: 1, lit: false, statuses: ["same"] }]]);
  assert.deepEqual([...(d.sides[1]?.east.get("1|e|k:library:pyoxigraph")?.flags ?? [])], [["library:pyoxigraph", { count: 1, lit: false, statuses: ["same"] }]]);
});

test("an expanded part shows its methods as rows and takes each call at its method", () => {
  const d = drawing(SHEET, state({ expanded: new Set([1]) }), FUNCTIONS);
  assert.deepEqual(d.rows[1], ["read", "write"]);
  assert.deepEqual(wires(d), ["0|e|c1 > 1|w|read", "0|e|c1 > 1|w|write", "1|e|read > 2|w|c1"]);
});

test("a flow details only its own calls, and gathers the others on the other-methods row", () => {
  const d = drawing(SHEET, state({ flow: FLOW }), FUNCTIONS);
  assert.deepEqual(d.rows, [["run", OTHER], ["read", OTHER], ["helper"]]);
  assert.deepEqual(wires(d), ["0|e|run > 1|w|read lit", `0|e|${OTHER} > 1|w|${OTHER}`, "1|e|read > 2|w|helper lit"]);
  assert.equal(d.facts?.entries.has("0|run"), true);
});

test("a part with rows fixes each port at the height of its row", () => {
  const d = drawing(SHEET, state({ expanded: new Set([1]) }), FUNCTIONS);
  const ledger = elkGraph(d, state({ expanded: new Set([1]) }), new Map()).root.children?.find((node) => node.id === "p1");
  assert.deepEqual(ledger?.ports?.map((port) => [port.id, port.y]), [
    ["1|w|read", ROWS_TOP],
    ["1|w|write", ROWS_TOP + ROW],
    ["1|e|read", ROWS_TOP],
    ["1|e|write", ROWS_TOP + ROW],
  ]);
});

test("grouping by role puts each part in its role's frame, in the order services, internal classes, modules", () => {
  const d = drawing(SHEET, state({ grouping: "role" }), FUNCTIONS);
  const graph = elkGraph(d, state({ grouping: "role" }), new Map());
  assert.deepEqual(graph.root.children?.map((node) => [node.id, node.layoutOptions?.["elk.partitioning.partition"]]), [
    ["g:service", "0"],
    ["g:internal", "1"],
    ["g:module", "2"],
  ]);
  assert.equal(graph.titles.get("g:internal"), "Internal classes");
});

test("a wire knows the calls it carries, so the panel can list them", () => {
  const d = drawing(SHEET, state(), FUNCTIONS);
  assert.deepEqual(d.wires[0]?.calls.map((call) => `${call.source_method}>${call.target_method} ${call.lines.join(",")}`), ["run>read 4,6", "run>write 7"]);
});

test("in a comparison a wire takes the status of its calls, and changed when they differ", () => {
  const comparison: SheetComparison = {
    base: "v1",
    head: "v2",
    sheet: SHEET,
    parts: SHEET.parts.map(() => ({ status: "same", methods: {}, doc: false, changes: 0 })),
    calls: ["added", "removed", "added"],
    inbound: ["removed"],
    outbound: ["same"],
  };
  const d = drawing(SHEET, state(), FUNCTIONS, comparison);
  assert.deepEqual(d.wires.map((w) => combine(w.statuses)), ["changed", "added"]);
  assert.deepEqual(d.sides[0]?.west.get("0|w|p:app")?.flags.get("app")?.statuses, ["removed"]);
});

test("a flow finds its parts by id, so it also fits a sheet merged from two states", () => {
  const merged: Sheet = { ...SHEET, parts: [{ ...at(SHEET.parts, 2), id: "shop.store.gone" }, ...SHEET.parts] };
  const d = drawing(merged, state({ flow: FLOW }), FUNCTIONS);
  assert.deepEqual([...(d.facts?.rows.keys() ?? [])], [1, 2, 3]);
});
