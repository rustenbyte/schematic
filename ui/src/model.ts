import type { ElkLabel, ElkNode, ElkPort, LayoutOptions } from "elkjs/lib/elk-api.js";
import type { Call, FlowPath, FunctionRef, Part, Sheet, SheetComparison, Status } from "./api.ts";

export const ROW = 14;
export const ROWS_TOP = 34;
export const OTHER = "…";
const CHAR = 5.4;
const FLAG_CHAR = 6.2;

export type Grouping = "none" | "role" | "file" | "service";

export interface ViewState {
  readonly grouping: Grouping;
  readonly expanded: ReadonlySet<number>;
  readonly flow: FlowPath | null;
}

export interface Point {
  readonly x: number;
  readonly y: number;
}

export function at<T>(list: readonly T[], index: number): T {
  const found = list[index];
  if (found === undefined) {
    throw new RangeError(`no item ${index} in a list of ${list.length}`);
  }
  return found;
}

export function callKey(source: number, sourceMethod: string, target: number, targetMethod: string): string {
  return `${source}|${sourceMethod}|${target}|${targetMethod}`;
}

function add(counts: Map<string, number>, key: string, count: number): void {
  counts.set(key, (counts.get(key) ?? 0) + count);
}

/** The status of something made of several items: theirs when they agree, else changed. */
export function combine(statuses: Iterable<Status>): Status {
  const seen = new Set(statuses);
  if (seen.size > 1) {
    return "changed";
  }
  return [...seen][0] ?? "same";
}

/** What one flow did on one sheet: the methods it ran, the calls it made, where it entered and what it raised. */
export class FlowFacts {
  readonly rows = new Map<number, Set<string>>();
  readonly calls = new Map<string, number>();
  readonly inbound = new Map<string, number>();
  readonly outbound = new Map<string, number>();
  readonly entries = new Set<string>();
  readonly raises = new Map<number, Set<string>>();

  static of(flow: FlowPath, functions: readonly FunctionRef[], sheet: Sheet): FlowFacts {
    const facts = new FlowFacts();
    const positions = new Map(sheet.parts.map((part, index) => [part.id, index]));
    const partOf = (ref: FunctionRef): number => (ref.part ? (positions.get(ref.part) ?? -1) : -1);
    for (const [callerIndex, calleeIndex, count] of flow.edges) {
      const callee = functions[calleeIndex];
      if (callee === undefined) {
        continue;
      }
      const target = partOf(callee);
      if (target >= 0) {
        facts.touch(target, callee.method);
      }
      const caller = callerIndex >= 0 ? functions[callerIndex] : undefined;
      if (caller === undefined) {
        if (target >= 0 && callerIndex < 0) {
          facts.entries.add(`${target}|${callee.method}`);
        }
        continue;
      }
      const source = partOf(caller);
      if (source >= 0) {
        facts.touch(source, caller.method);
      }
      if (source >= 0 && target >= 0 && source !== target) {
        add(facts.calls, callKey(source, caller.method, target, callee.method), count);
      }
      if (target >= 0 && source < 0) {
        add(facts.inbound, `${caller.sheet}|${target}|${callee.method}`, count);
      }
      if (source >= 0 && target < 0) {
        add(facts.outbound, `${source}|${caller.method}|sheet:${callee.sheet}`, count);
      }
    }
    for (const [index, exception, origin] of flow.raises) {
      const found = functions[index];
      const part = found === undefined ? -1 : partOf(found);
      if (origin && part >= 0) {
        const raised = facts.raises.get(part) ?? new Set<string>();
        raised.add(exception.split(".").pop() ?? exception);
        facts.raises.set(part, raised);
      }
    }
    return facts;
  }

  touched(part: number): boolean {
    return this.rows.has(part);
  }

  private touch(part: number, method: string): void {
    const methods = this.rows.get(part) ?? new Set<string>();
    methods.add(method);
    this.rows.set(part, methods);
  }
}

export interface Flag {
  count: number;
  lit: boolean;
  readonly statuses: Status[];
}

export interface PortInfo {
  readonly flags: Map<string, Flag>;
}

export interface Sides {
  readonly west: Map<string, PortInfo>;
  readonly east: Map<string, PortInfo>;
}

export interface Wire {
  readonly id: string;
  readonly source: number;
  readonly target: number;
  readonly sourcePort: string;
  readonly targetPort: string;
  count: number;
  lit: number;
  readonly methods: Set<string>;
  readonly calls: Call[];
  readonly statuses: Status[];
}

export function wireKey(wire: Pick<Wire, "sourcePort" | "targetPort">): string {
  return `${wire.sourcePort}>${wire.targetPort}`;
}

/** The sheet as it is drawn now: which parts show rows, their ports, and one wire per pair of ports. */
export interface Drawing {
  readonly sheet: Sheet;
  readonly comparison: SheetComparison | null;
  readonly facts: FlowFacts | null;
  readonly rows: readonly (readonly string[] | null)[];
  readonly sides: readonly Sides[];
  readonly wires: readonly Wire[];
}

function baseRows(part: Part, index: number, state: ViewState, facts: FlowFacts | null): string[] | null {
  if (part.kind === "package") {
    return null;
  }
  if (state.expanded.has(index)) {
    return [...part.methods];
  }
  const touched = facts?.rows.get(index);
  if (touched === undefined) {
    return null;
  }
  const extra = [...touched].filter((method) => !part.methods.includes(method)).sort();
  return [...part.methods.filter((method) => touched.has(method)), ...extra];
}

export function drawing(sheet: Sheet, state: ViewState, functions: readonly FunctionRef[], comparison: SheetComparison | null = null): Drawing {
  const facts = state.flow === null ? null : FlowFacts.of(state.flow, functions, sheet);
  const base = sheet.parts.map((part, index) => baseRows(part, index, state, facts));
  const usesOther = sheet.parts.map(() => false);
  const sides: Sides[] = sheet.parts.map(() => ({ west: new Map(), east: new Map() }));
  const wires = new Map<string, Wire>();

  const detailed = (part: number, method: string, lit: number): boolean =>
    (at(base, part)?.includes(method) ?? false) && (facts === null || lit > 0 || state.expanded.has(part));
  const end = (part: number, side: "w" | "e", method: string, neighbour: string, lit: number): string => {
    if (detailed(part, method, lit)) {
      return `${part}|${side}|${method}`;
    }
    if (at(base, part) !== null) {
      usesOther[part] = true;
      return `${part}|${side}|${OTHER}`;
    }
    return `${part}|${side}|${neighbour}`;
  };
  const port = (part: number, side: "w" | "e", id: string): PortInfo => {
    const ports = side === "w" ? at(sides, part).west : at(sides, part).east;
    const found = ports.get(id) ?? { flags: new Map<string, Flag>() };
    ports.set(id, found);
    return found;
  };
  const wire = (source: number, sourceMethod: string, target: number, targetMethod: string, count: number, lit: number, call: Call | null, status: Status): void => {
    const sourcePort = end(source, "e", sourceMethod, `c${target}`, lit);
    const targetPort = end(target, "w", targetMethod, `c${source}`, lit);
    port(source, "e", sourcePort);
    port(target, "w", targetPort);
    const key = wireKey({ sourcePort, targetPort });
    const found = wires.get(key) ?? { id: `w${wires.size}`, source, target, sourcePort, targetPort, count: 0, lit: 0, methods: new Set<string>(), calls: [], statuses: [] };
    found.count += count;
    found.lit += lit;
    found.methods.add(targetMethod);
    if (call !== null) {
      found.calls.push(call);
      found.statuses.push(status);
    }
    wires.set(key, found);
  };
  const flag = (info: PortInfo, key: string, count: number, lit: boolean, status: Status): void => {
    const found = info.flags.get(key) ?? { count: 0, lit: false, statuses: [] };
    found.count += count;
    found.lit = found.lit || lit;
    found.statuses.push(status);
    info.flags.set(key, found);
  };

  const seen = new Set<string>();
  sheet.calls.forEach((call, index) => {
    const key = callKey(call.source, call.source_method, call.target, call.target_method);
    seen.add(key);
    wire(call.source, call.source_method, call.target, call.target_method, call.count, facts?.calls.get(key) ?? 0, call, comparison?.calls[index] ?? "same");
  });
  for (const [key, lit] of facts?.calls ?? []) {
    if (seen.has(key)) {
      continue;
    }
    const [source = "", sourceMethod = "", target = "", targetMethod = ""] = key.split("|");
    wire(Number(source), sourceMethod, Number(target), targetMethod, 0, lit, null, "same");
  }
  sheet.inbound.forEach((call, index) => {
    const lit = facts?.inbound.has(`${call.sheet}|${call.target}|${call.target_method}`) ?? false;
    const id = end(call.target, "w", call.target_method, `p:${call.sheet}`, lit ? 1 : 0);
    flag(port(call.target, "w", id), call.sheet, call.count, lit, comparison?.inbound[index] ?? "same");
  });
  sheet.outbound.forEach((call, index) => {
    const lit = facts?.outbound.has(`${call.source}|${call.source_method}|${call.target}`) ?? false;
    const id = end(call.source, "e", call.source_method, `k:${call.target}`, lit ? 1 : 0);
    flag(port(call.source, "e", id), call.target, call.count, lit, comparison?.outbound[index] ?? "same");
  });
  for (const entry of facts?.entries ?? []) {
    const [part = "", method = ""] = entry.split("|");
    port(Number(part), "w", `${part}|w|${method}`);
  }
  const rows = base.map((found, index) => (found === null ? null : at(usesOther, index) ? [...found, OTHER] : found));
  return { sheet, comparison, facts, rows, sides, wires: [...wires.values()] };
}

export interface Group {
  readonly key: string;
  readonly title: string;
  readonly order: number | null;
}

const ROLE_GROUPS: Record<string, Group> = {
  service: { key: "service", title: "Services", order: 0 },
  internal: { key: "internal", title: "Internal classes", order: 1 },
  module: { key: "module", title: "Modules", order: 2 },
};

export function groupOf(part: Part, grouping: Grouping): Group | null {
  if (part.kind === "package" || grouping === "none") {
    return null;
  }
  if (grouping === "role") {
    return ROLE_GROUPS[part.role] ?? null;
  }
  if (grouping === "file") {
    return { key: part.file, title: part.file, order: null };
  }
  const shared = part.service === "Shared by services";
  const direct = part.service === "Reached from other sheets only";
  return { key: part.service, title: part.service, order: shared ? 1 : direct ? 2 : 0 };
}

/** The reference a drawing gives a part: U and its number, or on the overview S and its sheet's number. */
export function reference(part: Part, index: number): string {
  return part.kind === "package" ? `S${index + 2}` : `U${index + 1}`;
}

export function flagLabel(key: string): string {
  return key.slice(key.indexOf(":") + 1);
}

function flagRoom(info: PortInfo, side: "w" | "e"): ElkLabel[] {
  let width = 0;
  for (const key of info.flags.keys()) {
    width += (side === "w" ? key : flagLabel(key)).length * FLAG_CHAR + 24;
  }
  return width > 0 ? [{ text: "flags", width: Math.round(width + 8), height: 14 }] : [];
}

export interface Graph {
  readonly root: ElkNode;
  readonly titles: ReadonlyMap<string, string>;
}

/** The ELK input for a drawing; parts placed before keep their order, so a change moves as little as it can. */
export function elkGraph(d: Drawing, state: ViewState, placed: ReadonlyMap<string, Point>): Graph {
  const parts = d.sheet.parts;
  const order = parts.map((_, index) => index).sort((a, b) => {
    const pa = placed.get(at(parts, a).id);
    const pb = placed.get(at(parts, b).id);
    return pa !== undefined && pb !== undefined ? pa.x - pb.x || pa.y - pb.y : a - b;
  });
  const shapes = new Map<number, ElkNode>();
  for (const index of order) {
    const part = at(parts, index);
    const sides = at(d.sides, index);
    if (sides.west.size === 0 && sides.east.size === 0) {
      continue;
    }
    shapes.set(index, part.kind === "package" ? packageShape(part, index, sides) : shape(part, index, sides, at(d.rows, index)));
  }
  const children: ElkNode[] = [];
  const groups = new Map<string, ElkNode>();
  const titles = new Map<string, string>();
  for (const index of order) {
    const node = shapes.get(index);
    if (node === undefined) {
      continue;
    }
    const group = groupOf(at(parts, index), state.grouping);
    if (group === null) {
      children.push(node);
      continue;
    }
    let box = groups.get(group.key);
    if (box === undefined) {
      const options: LayoutOptions = { "elk.padding": "[top=40,left=20,bottom=20,right=20]" };
      if (group.order !== null) {
        options["elk.partitioning.partition"] = String(group.order);
      }
      box = { id: `g:${group.key}`, children: [], layoutOptions: options };
      groups.set(group.key, box);
      titles.set(box.id, group.title);
      children.push(box);
    }
    box.children?.push(node);
  }
  const options: LayoutOptions = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.edgeRouting": "ORTHOGONAL",
    "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.json.edgeCoords": "ROOT",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    "elk.layered.spacing.nodeNodeBetweenLayers": "56",
    "elk.spacing.nodeNode": "26",
    "elk.layered.spacing.edgeNodeBetweenLayers": "16",
    "elk.spacing.edgeEdge": "8",
    "elk.layered.spacing.edgeEdgeBetweenLayers": "8",
    "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    "elk.padding": "[top=50,left=50,bottom=50,right=50]",
  };
  if (state.grouping === "role" || state.grouping === "service") {
    options["elk.partitioning.activate"] = "true";
  }
  if (parts.length > 0 && parts.every((part) => part.kind === "package")) {
    options["elk.direction"] = "DOWN";
    options["elk.layered.spacing.nodeNodeBetweenLayers"] = "46";
    options["elk.spacing.nodeNode"] = "34";
  }
  const edges = d.wires
    .filter((w) => shapes.has(w.source) && shapes.has(w.target))
    .map((w) => ({ id: w.id, sources: [w.sourcePort], targets: [w.targetPort] }));
  return { root: { id: "root", layoutOptions: options, children, edges }, titles };
}

/** A package on the overview: calls from other packages enter at the top, its calls leave at the bottom, libraries hang right. */
function packageShape(part: Part, index: number, sides: Sides): ElkNode {
  const ports: ElkPort[] = [];
  let bottom = 0;
  let right = 0;
  for (const [id, info] of sides.west) {
    ports.push({ id, width: 1, height: 1, labels: flagRoom(info, "w"), layoutOptions: { "elk.port.side": "NORTH" } });
  }
  for (const [id, info] of sides.east) {
    const library = info.flags.size > 0;
    bottom += library ? 0 : 1;
    right += library ? 1 : 0;
    ports.push({ id, width: 1, height: 1, labels: flagRoom(info, "e"), layoutOptions: { "elk.port.side": library ? "EAST" : "SOUTH" } });
  }
  const width = Math.max(170, part.title.length * 10 + reference(part, index).length * 6 + 60, Math.max(sides.west.size, bottom) * 14 + 40);
  return {
    id: `p${index}`,
    width: Math.round(width),
    height: Math.max(48, 14 + right * 13),
    ports,
    layoutOptions: { "elk.portConstraints": "FIXED_SIDE", "elk.portLabels.placement": "OUTSIDE", "elk.spacing.portPort": "12" },
  };
}

function shape(part: Part, index: number, sides: Sides, rows: readonly string[] | null): ElkNode {
  let width = Math.max(96, part.title.length * 7.2 + reference(part, index).length * 5.5 + 46);
  const ports: ElkPort[] = [];
  if (rows === null) {
    const height = Math.max(30, 10 + Math.max(sides.west.size, sides.east.size) * 11);
    for (const [id, info] of sides.west) {
      ports.push({ id, width: 1, height: 1, labels: flagRoom(info, "w"), layoutOptions: { "elk.port.side": "WEST" } });
    }
    for (const [id, info] of sides.east) {
      ports.push({ id, width: 1, height: 1, labels: flagRoom(info, "e"), layoutOptions: { "elk.port.side": "EAST" } });
    }
    return {
      id: `p${index}`,
      width: Math.round(width),
      height: Math.round(height),
      ports,
      layoutOptions: { "elk.portConstraints": "FIXED_SIDE", "elk.portLabels.placement": "OUTSIDE", "elk.spacing.portPort": "10" },
    };
  }
  width = Math.max(width, Math.max(4, ...rows.map((row) => row.length)) * CHAR + 28);
  const rowOf = (id: string): number => Math.max(0, rows.indexOf(id.slice(id.lastIndexOf("|") + 1)));
  for (const [id, info] of sides.west) {
    ports.push({ id, x: -1, y: ROWS_TOP + rowOf(id) * ROW, width: 1, height: 1, labels: flagRoom(info, "w"), layoutOptions: { "elk.port.side": "WEST" } });
  }
  for (const [id, info] of sides.east) {
    ports.push({ id, x: Math.round(width), y: ROWS_TOP + rowOf(id) * ROW, width: 1, height: 1, labels: flagRoom(info, "e"), layoutOptions: { "elk.port.side": "EAST" } });
  }
  return {
    id: `p${index}`,
    width: Math.round(width),
    height: ROWS_TOP + rows.length * ROW + 6,
    ports,
    layoutOptions: { "elk.portConstraints": "FIXED_POS", "elk.portLabels.placement": "OUTSIDE" },
  };
}
