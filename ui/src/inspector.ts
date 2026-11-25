import type { FlowPath, Part, PartDetail, Sheet, SheetIndex } from "./api.ts";
import type { Drawing, Wire } from "./model.ts";
import { at, flagLabel } from "./model.ts";
import { readableTest, relativePath, shortName } from "./names.ts";

export interface PanelContext {
  readonly index: SheetIndex;
  readonly sheet: Sheet;
  readonly drawing: Drawing;
  readonly flow: FlowPath | null;
  readonly flows: number;
  readonly expanded: ReadonlySet<number>;
}

export interface PanelEvents {
  readonly open: (sheetId: string) => void;
  readonly selectPart: (index: number) => void;
  readonly toggle: (index: number) => void;
  readonly clearFlow: () => void;
}

const ROLE_NAMES: Readonly<Record<string, string>> = {
  service: "Service",
  internal: "Internal class",
  module: "Module",
  package: "Package",
};

type Content = Node | string | null;

function node<K extends keyof HTMLElementTagNameMap>(name: K, children: readonly Content[] = [], className = ""): HTMLElementTagNameMap[K] {
  const found = document.createElement(name);
  if (className) {
    found.className = className;
  }
  for (const child of children) {
    if (child !== null) {
      found.append(child);
    }
  }
  return found;
}

/** Writes a docstring as paragraphs, with `code` in backticks set as code. */
function prose(text: string): HTMLElement {
  const box = node("div", [], "doc");
  for (const paragraph of text.split(/\n\s*\n/)) {
    const p = node("p");
    paragraph.replace(/\s+/g, " ").trim().split("`").forEach((piece, index) => {
      p.append(index % 2 === 1 ? node("code", [piece]) : piece);
    });
    box.append(p);
  }
  return box;
}

function firstParagraph(text: string): string {
  return (text.split(/\n\s*\n/)[0] ?? text).replace(/\s+/g, " ").trim();
}

function tally<K>(entries: Iterable<readonly [K, number]>): Map<K, number> {
  const counts = new Map<K, number>();
  for (const [key, count] of entries) {
    counts.set(key, (counts.get(key) ?? 0) + count);
  }
  return counts;
}

/** The detail panel: what the selected part, wire or flow is, or what the sheet holds when nothing is selected. */
export class Inspector {
  private readonly box: HTMLElement;
  private readonly events: PanelEvents;

  constructor(box: HTMLElement, events: PanelEvents) {
    this.box = box;
    this.events = events;
  }

  showSheet(c: PanelContext): void {
    const sheet = c.sheet;
    const overview = sheet.id === c.index.overview;
    const libraries = new Set(sheet.outbound.filter((o) => o.target.startsWith("library:")).map((o) => o.target));
    const rows: [string, string][] = overview
      ? [
          ["Packages", String(sheet.parts.length)],
          ["Wires", String(sheet.calls.length)],
          ["Libraries", String(libraries.size)],
          ["Flows", String(c.flows)],
        ]
      : [
          ["Services", String(sheet.parts.filter((p) => p.role === "service").length)],
          ["Internal classes", String(sheet.parts.filter((p) => p.role === "internal").length)],
          ["Modules", String(sheet.parts.filter((p) => p.role === "module").length)],
          ["Calls inside", String(sheet.calls.reduce((sum, call) => sum + call.count, 0))],
          ["From other sheets", String(new Set(sheet.inbound.map((i) => i.sheet)).size)],
          ["To other sheets", String(new Set(sheet.outbound.filter((o) => o.target.startsWith("sheet:")).map((o) => o.target)).size)],
          ["Libraries", String(libraries.size)],
          ["Flows", String(c.flows)],
        ];
    this.replace(this.head(["Sheet"], overview ? c.index.project : sheet.title, overview ? c.index.root : null), this.facts(rows));
  }

  showFlow(c: PanelContext, flow: FlowPath): void {
    const facts = c.drawing.facts;
    const words = flow.name === flow.test ? readableTest(flow.test) : { name: flow.name, param: "" };
    const raised = [...(facts?.raises ?? [])].flatMap(([index, names]) => [...names].map((name) => `${at(c.sheet.parts, index).title}: ${name}`));
    const entries = [...(facts?.entries ?? [])].map((entry) => {
      const [part = "0", method = ""] = entry.split("|");
      return `${at(c.sheet.parts, Number(part)).title}${method ? `.${method}` : ""}`;
    });
    this.replace(
      this.head(["Flow", flow.outcome], words.param ? `${words.name} (${words.param})` : words.name, node("code", [flow.test])),
      this.facts([
        ["Parts it ran", String(facts?.rows.size ?? 0)],
        ["Calls on this sheet", String([...(facts?.calls.values() ?? [])].reduce((sum, n) => sum + n, 0))],
      ]),
      entries.length ? this.section("Entered at", this.list(entries.map((entry) => [entry]))) : null,
      raised.length ? this.section("Raises", this.list(raised.map((name) => [name]), "fault-list")) : null,
      node("div", [this.button("Clear flow", () => this.events.clearFlow(), "ghost")], "actions"),
    );
  }

  showPart(c: PanelContext, index: number, detail: PartDetail | null): void {
    const part = at(c.sheet.parts, index);
    const project = c.index.project;
    const where = detail?.line ?? null;
    this.replace(
      this.head([ROLE_NAMES[part.role] ?? part.role], part.title, part.path ? this.link(c, part.path, where) : null),
      detail?.doc ? prose(detail.doc) : null,
      detail && detail.kinds.length ? node("div", detail.kinds.map((kind) => node("span", [kind], "tag")), "tags") : null,
      detail && detail.implements.length ? this.section("Implements", this.list(detail.implements.map((name) => [shortName(project, name)]))) : null,
      detail && detail.built_with.length
        ? this.section("Built with", node("div", detail.built_with.flatMap((w) => [node("span", [w.name], "name"), node("span", [shortName(project, w.type)], "type")]), "pairs"))
        : null,
      detail && detail.held_by.length ? this.section("Held by", this.list(detail.held_by.map((name) => [shortName(project, name)]))) : null,
      this.connections(c, index, part),
      detail ? this.members(c, part, detail) : node("p", ["Loading"], "quiet"),
      detail && detail.raises.length ? this.section("Raises", this.list(detail.raises.map((name) => [shortName(project, name)]), "fault-list")) : null,
      detail && detail.types.length ? this.section("Data types", this.list(detail.types.map((name) => [shortName(project, name)]))) : null,
      this.flowPart(c, index),
      node("div", [part.kind === "package"
        ? this.button("Open sheet", () => this.events.open(part.id), "primary")
        : this.button(c.expanded.has(index) ? "Collapse" : "Expand", () => this.events.toggle(index), "ghost")], "actions"),
    );
  }

  showWire(c: PanelContext, wire: Wire): void {
    const source = at(c.sheet.parts, wire.source);
    const target = at(c.sheet.parts, wire.target);
    const rows = wire.calls.map((call) => [
      node("code", [call.source_method && call.target_method ? `${call.source_method} → ${call.target_method}` : `${source.title} → ${target.title}`]),
      node("span", [call.lines.length ? "" : `×${call.count}`, ...call.lines.flatMap((line, i) => [i ? " " : "", this.link(c, source.path, line, `:${line}`)])], "n"),
    ]);
    this.replace(
      this.head(["Wire", ...(wire.lit > 0 ? [`${wire.lit} calls in this flow`] : [])], `${source.title} to ${target.title}`, null),
      this.facts([["Calls", String(wire.calls.reduce((sum, call) => sum + call.count, 0))]]),
      rows.length ? this.section("Calls", this.list(rows, "calls")) : node("p", ["Recorded in this flow; the scan does not resolve these calls."], "quiet"),
      node("div", [
        this.button(source.title, () => this.events.selectPart(wire.source), "ghost"),
        this.button(target.title, () => this.events.selectPart(wire.target), "ghost"),
      ], "actions"),
    );
  }

  private connections(c: PanelContext, index: number, part: Part): HTMLElement | null {
    const sheet = c.sheet;
    const out = tally(sheet.calls.filter((call) => call.source === index).map((call): [number, number] => [call.target, call.count]));
    const into = tally(sheet.calls.filter((call) => call.target === index).map((call): [number, number] => [call.source, call.count]));
    const from = tally(sheet.inbound.filter((call) => call.target === index).map((call): [string, number] => [call.sheet, call.count]));
    const to = tally(sheet.outbound.filter((call) => call.source === index).map((call): [string, number] => [call.target, call.count]));
    const partRows = (counts: Map<number, number>): Content[][] =>
      [...counts].map(([other, count]) => [this.button(at(sheet.parts, other).title, () => this.events.selectPart(other), "link"), node("span", [String(count)], "n")]);
    const sections = [
      out.size ? this.section(part.kind === "package" ? "Calls into" : "Calls", this.list(partRows(out))) : null,
      into.size ? this.section("Called by", this.list(partRows(into))) : null,
      from.size ? this.section("Called from other sheets", this.list([...from].map(([sheetId, count]) => [this.button(sheetId, () => this.events.open(sheetId), "link"), node("span", [String(count)], "n")]))) : null,
      to.size
        ? this.section("Calls to other sheets and libraries", this.list([...to].map(([key, count]) => [
            key.startsWith("sheet:") ? this.button(flagLabel(key), () => this.events.open(flagLabel(key)), "link") : flagLabel(key),
            node("span", [String(count)], "n"),
          ])))
        : null,
    ].filter((found) => found !== null);
    return sections.length ? node("div", sections) : null;
  }

  private members(c: PanelContext, part: Part, detail: PartDetail): HTMLElement | null {
    if (detail.members.length === 0) {
      return null;
    }
    const items = detail.members.map((member) => {
      const wired = part.methods.includes(member.name);
      return node("li", [
        node("div", [node("code", [member.name], wired ? "wired" : ""), node("span", [this.link(c, detail.path, member.line, `:${member.line}`)], "n")], "member"),
        member.doc ? node("div", [firstParagraph(member.doc)], "member-doc") : null,
      ]);
    });
    return this.section(part.kind === "module" ? "Functions" : "Methods", node("ul", items, "members"));
  }

  private flowPart(c: PanelContext, index: number): HTMLElement | null {
    const methods = c.drawing.facts?.rows.get(index);
    if (methods === undefined || methods.size === 0) {
      return null;
    }
    return this.section("In this flow", this.list([...methods].filter(Boolean).map((method) => [node("code", [method])])));
  }

  private head(kicker: readonly string[], title: string, sub: Content): HTMLElement {
    return node("div", [
      node("div", kicker.map((word) => node("span", [word])), "kicker"),
      node("h1", [title]),
      sub === null ? null : node("div", [sub], "path"),
    ], "head");
  }

  private section(title: string, body: Node): HTMLElement {
    return node("section", [node("h3", [title]), body]);
  }

  private facts(rows: readonly (readonly [string, string])[]): HTMLElement {
    return node("section", [node("dl", rows.flatMap(([term, value]) => [node("dt", [term]), node("dd", [value])]), "facts")]);
  }

  private list(rows: readonly Content[][], className = ""): HTMLElement {
    return node("ul", rows.map((row) => node("li", row)), className);
  }

  private button(label: string, action: () => void, className: string): HTMLButtonElement {
    const found = node("button", [label], className);
    found.addEventListener("click", action);
    return found;
  }

  private link(c: PanelContext, path: string, line: number | null, label?: string): HTMLAnchorElement {
    const found = node("a", [label ?? `${relativePath(c.index.root, path)}${line === null ? "" : `:${line}`}`]);
    found.href = `vscode://file${path}${line === null ? "" : `:${line}`}`;
    return found;
  }

  private replace(...children: Content[]): void {
    this.box.replaceChildren(...children.filter((child): child is Node | string => child !== null));
  }
}
