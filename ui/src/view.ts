import type { Status } from "./api.ts";
import type { Layout, PlacedPart } from "./layout.ts";
import type { Drawing, Point, PortInfo, Wire } from "./model.ts";
import { OTHER, ROW, ROWS_TOP, at, combine, flagLabel, reference, wireKey } from "./model.ts";
import { cloud, element, path, text } from "./svg.ts";

const FLAG_CHAR = 6.2;
const ZONE = 260;
const REF_CHAR = 5.6;

function letter(index: number): string {
  return String.fromCharCode(65 + (index % 26));
}

export interface SheetEvents {
  readonly part: (index: number) => void;
  readonly wire: (wire: Wire) => void;
  readonly clear: () => void;
  readonly dragging: () => boolean;
}

export type Selection = { readonly part: number } | { readonly wire: string } | null;

type Focus = { readonly part: number } | { readonly wire: Wire } | null;

/** Draws one sheet: groups, wires with their junctions, parts with rows, pins and flags, and a flow's current. */
export class SheetView {
  private readonly svg: SVGSVGElement;
  private readonly events: SheetEvents;
  private adjacency = new Map<number, Wire[]>();
  private wires = new Map<string, Wire>();
  private selection: Selection = null;

  constructor(svg: SVGSVGElement, events: SheetEvents) {
    this.svg = svg;
    this.events = events;
  }

  draw(d: Drawing, layout: Layout, previous: ReadonlyMap<string, Point>): SVGGElement {
    this.svg.textContent = "";
    const defs = element("defs", {}, this.svg);
    const minor = element("pattern", { id: "minor", width: 10, height: 10, patternUnits: "userSpaceOnUse" }, defs);
    element("path", { d: "M10 0H0V10", class: "grid-minor" }, minor);
    const major = element("pattern", { id: "major", width: 50, height: 50, patternUnits: "userSpaceOnUse" }, defs);
    element("rect", { width: 50, height: 50, fill: "url(#minor)" }, major);
    element("path", { d: "M50 0H0V50", class: "grid-major" }, major);
    const view = element("g", {}, this.svg);
    const desk = element("rect", { x: -8000, y: -8000, width: layout.width + 16000, height: layout.height + 16000, class: "desk" }, view);
    const sheet = element("rect", { x: 0, y: 0, width: layout.width, height: layout.height, class: "sheet" }, view);
    for (const surface of [desk, sheet]) {
      surface.addEventListener("click", () => {
        if (!this.events.dragging()) {
          this.events.clear();
        }
      });
    }
    element("rect", { x: 0, y: 0, width: layout.width, height: layout.height, fill: "url(#major)", "pointer-events": "none" }, view);
    this.frame(view, layout.width, layout.height);
    const groups = element("g", { class: "fade" }, view);
    const wires = element("g", { class: "fade" }, view);
    const parts = element("g", {}, view);
    const marks = element("g", { class: "fade" }, view);
    for (const group of layout.groups.values()) {
      element("rect", { x: group.x, y: group.y, width: group.w, height: group.h, rx: 6, class: "group" }, groups);
      text(groups, group.x + 14, group.y + 23, group.title, "group-title");
    }
    const flowing = d.facts !== null;
    this.svg.classList.toggle("flowing", flowing);
    this.svg.classList.toggle("comparing", d.comparison !== null);
    this.adjacency = new Map();
    this.wires = new Map(d.wires.map((wire) => [wireKey(wire), wire]));
    for (const wire of d.wires) {
      const route = layout.routes.get(wire.id);
      if (route === undefined || route.sections.length === 0) {
        continue;
      }
      for (const end of [wire.source, wire.target]) {
        this.adjacency.set(end, [...(this.adjacency.get(end) ?? []), wire]);
      }
      const lit = flowing && wire.lit > 0;
      const status = combine(wire.statuses);
      const line = path(route.sections);
      if (lit) {
        element("path", { d: line, class: "halo" }, wires);
      }
      element("path", { d: line, class: `wire ${status}${lit ? " lit" : ""}`, "data-wire": wire.id }, wires);
      const hit = element("path", { d: line, class: "hit" }, wires);
      hit.addEventListener("mouseenter", () => this.focus({ wire }));
      hit.addEventListener("mouseleave", () => this.focus(null));
      hit.addEventListener("click", (event) => {
        event.stopPropagation();
        if (!this.events.dragging()) {
          this.events.wire(wire);
        }
      });
      for (const p of route.junctions) {
        element("circle", { cx: p.x, cy: p.y, r: 2.3, class: `junction ${status}${lit ? " lit" : ""}`, "data-wire": wire.id }, wires);
      }
      const last = route.sections[route.sections.length - 1] ?? [];
      const a = last[last.length - 2];
      const b = last[last.length - 1];
      if (lit && wire.lit > 1 && a !== undefined && b !== undefined) {
        text(marks, (a.x + b.x) / 2, Math.min(a.y, b.y) - 4, `×${wire.lit}`, "count", { "text-anchor": "middle" });
      }
    }
    for (const [index, placed] of layout.parts) {
      const id = at(d.sheet.parts, index).id;
      const before = previous.get(id);
      const g = this.part(parts, index, placed, d);
      g.style.transform = `translate(${before?.x ?? placed.x}px, ${before?.y ?? placed.y}px)`;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        g.style.transform = `translate(${placed.x}px, ${placed.y}px)`;
      }));
    }
    for (const [index, raised] of d.facts?.raises ?? []) {
      const placed = layout.parts.get(index);
      if (placed !== undefined) {
        text(marks, placed.x + placed.w, placed.y - 6, `raises ${[...raised].join(", ")}`, "fault", { "text-anchor": "end" });
      }
    }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      for (const layer of [groups, wires, marks]) {
        layer.classList.add("shown");
      }
    }));
    this.focus(null);
    return view;
  }

  select(selection: Selection): void {
    this.selection = selection;
    this.focus(null);
  }

  private part(parent: SVGGElement, index: number, placed: PlacedPart, d: Drawing): SVGGElement {
    const part = at(d.sheet.parts, index);
    const rows = at(d.rows, index);
    const facts = d.facts;
    const warm = facts?.touched(index) ?? false;
    const opens = part.kind === "package";
    const change = d.comparison?.parts[index];
    const status = change?.status ?? "same";
    const g = element("g", {
      class: `part ${part.kind} ${status}${warm ? " warm" : ""}`,
      "data-part": index,
      tabindex: 0,
      role: "button",
      "aria-label": opens ? `Open sheet ${part.title}` : `${rows === null ? "Expand" : "Collapse"} ${part.title}`,
    }, parent);
    g.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!this.events.dragging()) {
        this.events.part(index);
      }
    });
    g.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.events.part(index);
      }
    });
    g.addEventListener("mouseenter", () => this.focus({ part: index }));
    g.addEventListener("mouseleave", () => this.focus(null));
    const { w, h } = placed;
    if (status === "changed") {
      element("path", { d: cloud(-10, -10, w + 20, h + 20, 7), class: "cloud" }, g);
      text(g, -16, -12, "Δ", "delta");
    }
    if (part.kind === "module") {
      element("path", { d: `M0 0H${w - 8}L${w} 8V${h}H0Z`, class: "body" }, g);
      element("rect", { x: 0.6, y: 0.6, width: w - 9, height: rows === null ? h - 1.2 : 25, class: "band" }, g);
    } else {
      element("rect", { x: 0, y: 0, width: w, height: h, rx: 1.5, class: "body" }, g);
      if (part.kind === "class") {
        element("path", { d: `M${w / 2 - 6} 0A6 6 0 0 0 ${w / 2 + 6} 0`, class: "body" }, g);
      }
    }
    const ref = reference(part, index);
    const titleX = 9 + ref.length * REF_CHAR + 6;
    if (rows === null) {
      text(g, 9, h / 2 + 4, ref, "ref");
      text(g, titleX, h / 2 + 4.3, part.title, "title");
      text(g, w - 10, h / 2 + 4, opens ? "›" : "+", "toggle", { "text-anchor": "end" });
    } else {
      element("line", { x1: 0, y1: 26, x2: w, y2: 26, class: "rule" }, g);
      text(g, 9, 17.3, ref, "ref");
      text(g, titleX, 17.5, part.title, "title");
      text(g, w - 10, 17.5, "−", "toggle", { "text-anchor": "end" });
      rows.forEach((row, r) => {
        const lit = facts?.rows.get(index)?.has(row) ?? false;
        const other = row === OTHER;
        const method = change?.methods[row] ?? "same";
        const mark = method === "added" ? "+ " : method === "removed" ? "− " : "";
        text(g, 8, ROWS_TOP + r * ROW + 3.6, other ? "other methods" : `${mark}${row}`, `row ${method}${other ? " other" : ""}${lit ? " lit" : ""}`);
      });
    }
    const sides = at(d.sides, index);
    for (const [id, port] of placed.ports) {
      const west = id.includes("|w|");
      const info = (west ? sides.west : sides.east).get(id);
      if (port.y < 0.5 || port.y > h - 0.5) {
        const down = port.y > h - 0.5;
        element("line", { x1: port.x, y1: down ? h : -6, x2: port.x, y2: down ? h + 6 : 0, class: "pin" }, g);
        element("circle", { cx: port.x, cy: down ? h + 6 : -6, r: 1.8, class: "dot" }, g);
        continue;
      }
      if (west) {
        this.westPin(g, index, id, port.y, info, d);
      } else {
        this.eastPin(g, w, port.y, info);
      }
    }
    return g;
  }

  /** The sheet's border, with zones lettered down the side and numbered along the top, as a drawing has them. */
  private frame(parent: SVGGElement, width: number, height: number): void {
    const outer = 6;
    const inner = 18;
    element("rect", { x: outer, y: outer, width: width - 2 * outer, height: height - 2 * outer, class: "frame" }, parent);
    element("rect", { x: inner, y: inner, width: width - 2 * inner, height: height - 2 * inner, class: "frame-inner" }, parent);
    const columns = Math.max(1, Math.round((width - 2 * inner) / ZONE));
    const rows = Math.max(1, Math.round((height - 2 * inner) / ZONE));
    const across = (width - 2 * inner) / columns;
    const down = (height - 2 * inner) / rows;
    for (let c = 0; c < columns; c++) {
      const x = inner + c * across;
      if (c > 0) {
        element("line", { x1: x, y1: outer, x2: x, y2: inner, class: "zone-tick" }, parent);
        element("line", { x1: x, y1: height - inner, x2: x, y2: height - outer, class: "zone-tick" }, parent);
      }
      text(parent, x + across / 2, 15.5, String(c + 1), "zone");
      text(parent, x + across / 2, height - 8.5, String(c + 1), "zone");
    }
    for (let r = 0; r < rows; r++) {
      const y = inner + r * down;
      if (r > 0) {
        element("line", { x1: outer, y1: y, x2: inner, y2: y, class: "zone-tick" }, parent);
        element("line", { x1: width - inner, y1: y, x2: width - outer, y2: y, class: "zone-tick" }, parent);
      }
      text(parent, 12, y + down / 2 + 4, letter(r), "zone");
      text(parent, width - 12, y + down / 2 + 4, letter(r), "zone");
    }
  }

  private westPin(g: SVGGElement, index: number, id: string, y: number, info: PortInfo | undefined, d: Drawing): void {
    element("line", { x1: -6, y1: y, x2: 0, y2: y, class: "pin" }, g);
    element("circle", { cx: -6, cy: y, r: 1.8, class: "dot" }, g);
    if (d.facts?.entries.has(`${index}|${id.slice(id.lastIndexOf("|") + 1)}`)) {
      element("circle", { cx: -20, cy: y, r: 6, class: "entry" }, g);
      element("path", { d: `M-23.5 ${y}q1.75 -4 3.5 0t3.5 0`, class: "entry" }, g);
      element("line", { x1: -14, y1: y, x2: -6, y2: y, class: "entry" }, g);
    }
    if (info === undefined || info.flags.size === 0) {
      return;
    }
    let x = -10;
    element("line", { x1: x, y1: y, x2: -6, y2: y, class: "stub" }, g);
    for (const [key, flag] of [...info.flags].reverse()) {
      const width = key.length * FLAG_CHAR + 20;
      x -= width + 4;
      this.flag(g, x, y, width, key, flag.lit, "sheet", combine(flag.statuses));
    }
  }

  private eastPin(g: SVGGElement, w: number, y: number, info: PortInfo | undefined): void {
    element("line", { x1: w, y1: y, x2: w + 6, y2: y, class: "pin" }, g);
    element("circle", { cx: w + 6, cy: y, r: 1.8, class: "dot" }, g);
    if (info === undefined || info.flags.size === 0) {
      return;
    }
    let x = w + 10;
    element("line", { x1: w + 6, y1: y, x2: x, y2: y, class: "stub" }, g);
    for (const [key, flag] of info.flags) {
      const label = flagLabel(key);
      const width = label.length * FLAG_CHAR + 20;
      this.flag(g, x, y, width, label, flag.lit, key.startsWith("library:") ? "library" : "sheet", combine(flag.statuses));
      x += width + 4;
    }
  }

  private flag(g: SVGGElement, x: number, y: number, width: number, label: string, lit: boolean, kind: "sheet" | "library", status: Status): void {
    const shape = kind === "library"
      ? `M${x + 6.5} ${y - 6.5}H${x + width - 6.5}A6.5 6.5 0 0 1 ${x + width - 6.5} ${y + 6.5}H${x + 6.5}A6.5 6.5 0 0 1 ${x + 6.5} ${y - 6.5}Z`
      : `M${x} ${y - 6.5}H${x + width - 7}L${x + width} ${y}L${x + width - 7} ${y + 6.5}H${x}Z`;
    element("path", { d: shape, class: `flag ${status}${lit ? " lit" : ""}` }, g);
    text(g, x + 6, y + 3.4, label, `flag-text ${status}${lit ? " lit" : ""}`);
  }

  private focus(hovered: Focus): void {
    const target = hovered ?? this.pinned();
    this.svg.classList.toggle("focusing", target !== null);
    const parts = new Set<number>();
    const wires = new Set<string>();
    if (target !== null && "part" in target) {
      parts.add(target.part);
      for (const wire of this.adjacency.get(target.part) ?? []) {
        wires.add(wire.id);
        parts.add(wire.source);
        parts.add(wire.target);
      }
    }
    if (target !== null && "wire" in target) {
      wires.add(target.wire.id);
      parts.add(target.wire.source);
      parts.add(target.wire.target);
    }
    const selected = this.selection;
    const selectedWire = selected !== null && "wire" in selected ? this.wires.get(selected.wire)?.id : undefined;
    for (const node of this.svg.querySelectorAll("[data-part]")) {
      const index = Number(node.getAttribute("data-part"));
      node.classList.toggle("near", parts.has(index));
      node.classList.toggle("selected", selected !== null && "part" in selected && selected.part === index);
    }
    for (const node of this.svg.querySelectorAll("[data-wire]")) {
      const id = node.getAttribute("data-wire") ?? "";
      node.classList.toggle("near", wires.has(id));
      node.classList.toggle("selected", id === selectedWire);
    }
  }

  private pinned(): Focus {
    const selected = this.selection;
    if (selected === null) {
      return null;
    }
    if ("part" in selected) {
      return { part: selected.part };
    }
    const wire = this.wires.get(selected.wire);
    return wire === undefined ? null : { wire };
  }
}
