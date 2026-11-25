import type {
  CallData,
  ComparisonIndex,
  FlowPath,
  PartDetail,
  Sheet,
  SheetComparison,
  SheetFlows,
  SheetIndex,
  State,
  StateList,
} from "./api.ts";
import { fetchJson } from "./api.ts";
import { Inspector } from "./inspector.ts";
import type { PanelContext, WireData } from "./inspector.ts";
import { LayoutEngine } from "./layout.ts";
import { Panels } from "./panels.ts";
import type { Drawing, Grouping, Point, ViewState, Wire } from "./model.ts";
import { at, combine, drawing, elkGraph, wireKey } from "./model.ts";
import { readableTest } from "./names.ts";
import { Theme } from "./theme.ts";
import { SheetView } from "./view.ts";
import type { Selection } from "./view.ts";
import { Viewport } from "./viewport.ts";

const GROUPINGS: readonly Grouping[] = ["none", "role", "file", "service"];
const NO_FLOWS: SheetFlows = { functions: [], flows: [] };

function byId<T extends Element>(id: string, kind: { new (): T; prototype: T }): T {
  const found = document.getElementById(id);
  if (!(found instanceof kind)) {
    throw new Error(`the page has no #${id}`);
  }
  return found;
}

function html<K extends keyof HTMLElementTagNameMap>(name: K, text = "", className = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(name);
  node.textContent = text;
  if (className) {
    node.className = className;
  }
  return node;
}

function part(value: string): string {
  return encodeURIComponent(value);
}

/** The page: picks the states, the sheet, the flow, the grouping and the expanded parts, and has the sheet drawn. */
class App {
  private readonly svg = byId("drawing", SVGSVGElement);
  private readonly stage = byId("stage", HTMLElement);
  private readonly sheetList = byId("sheets", HTMLOListElement);
  private readonly flowList = byId("flows", HTMLDivElement);
  private readonly filter = byId("filter", HTMLInputElement);
  private readonly crumbs = byId("crumbs", HTMLElement);
  private readonly titleBlock = byId("title-block", HTMLDivElement);
  private readonly sheetControls = byId("sheet-controls", HTMLDivElement);
  private readonly clearFlow = byId("clear-flow", HTMLButtonElement);
  private readonly notice = byId("notice", HTMLParagraphElement);
  private readonly headSelect = byId("head", HTMLSelectElement);
  private readonly baseSelect = byId("base", HTMLSelectElement);
  private readonly engine = new LayoutEngine();
  private readonly viewport = new Viewport(this.svg, this.stage);
  private readonly view = new SheetView(this.svg, {
    part: (index) => this.onPart(index),
    wire: (wire) => this.select({ wire: wireKey(wire) }),
    clear: () => this.select(null),
    dragging: () => this.viewport.dragging,
  });
  private readonly inspector = new Inspector(byId("inspector", HTMLDivElement), {
    open: (sheetId) => this.go(this.open(sheetId)),
    selectPart: (index) => this.select({ part: index }),
    toggle: (index) => this.toggle(index),
    clearFlow: () => this.chooseFlow(null),
  });
  private readonly panels = new Panels(
    document.body,
    {
      buttons: { rail: byId("toggle-rail", HTMLButtonElement), side: byId("toggle-side", HTMLButtonElement) },
      panels: { rail: byId("rail", HTMLElement), side: byId("side", HTMLElement) },
      handles: { rail: byId("resize-rail", HTMLDivElement), side: byId("resize-side", HTMLDivElement) },
    },
    {
      moved: (name, dx) => {
        if (name === "rail") {
          this.viewport.pan(-dx);
        }
      },
    },
  );
  private readonly theme = new Theme(document.documentElement, [...document.querySelectorAll<HTMLButtonElement>("[data-theme-choice]")]);
  private readonly expanded = new Set<number>();
  private states: StateList = { current: "", states: [] };
  private head = "";
  private base: string | null = null;
  private index: SheetIndex | null = null;
  private changes: ComparisonIndex | null = null;
  private sheet: Sheet | null = null;
  private comparison: SheetComparison | null = null;
  private flows: SheetFlows = NO_FLOWS;
  private grouping: Grouping = "none";
  private flow: FlowPath | null = null;
  private placed = new Map<string, Point>();
  private run = 0;
  private current: Drawing | null = null;
  private selection: Selection = null;
  private asked = 0;

  constructor() {
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-grouping]")) {
      button.addEventListener("click", () => {
        this.grouping = GROUPINGS.find((grouping) => grouping === button.dataset["grouping"]) ?? "none";
        this.renderControls();
        void this.relayout(true);
      });
    }
    byId("expand-all", HTMLButtonElement).addEventListener("click", () => {
      this.sheet?.parts.forEach((_, index) => this.expanded.add(index));
      void this.relayout(true);
    });
    byId("collapse-all", HTMLButtonElement).addEventListener("click", () => {
      this.expanded.clear();
      void this.relayout(true);
    });
    byId("zoom-in", HTMLButtonElement).addEventListener("click", () => this.viewport.zoomBy(1.25));
    byId("zoom-out", HTMLButtonElement).addEventListener("click", () => this.viewport.zoomBy(0.8));
    byId("zoom-fit", HTMLButtonElement).addEventListener("click", () => void this.relayout(true));
    this.clearFlow.addEventListener("click", () => this.chooseFlow(null));
    this.filter.addEventListener("input", () => this.renderFlows());
    this.headSelect.addEventListener("change", () => {
      this.head = this.headSelect.value;
      this.base = this.base === this.head ? null : this.base;
      this.go(this.reload());
    });
    this.baseSelect.addEventListener("change", () => {
      this.base = this.baseSelect.value || null;
      this.go(this.reload());
    });
    document.addEventListener("keydown", (event) => {
      const typing = event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement;
      if (typing || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      if (event.key === "Escape") {
        this.select(null);
      }
      if (event.key === "[") {
        this.panels.toggle("rail");
      }
      if (event.key === "]") {
        this.panels.toggle("side");
      }
    });
  }

  async start(): Promise<void> {
    this.states = await fetchJson<StateList>("/api/states");
    const hash = new URLSearchParams(location.hash.slice(1));
    const known = (id: string | null): string | null => (id !== null && this.states.states.some((s) => s.id === id) ? id : null);
    this.head = known(hash.get("head")) ?? this.states.current;
    this.base = known(hash.get("base"));
    this.renderStates();
    await this.loadIndex();
    await this.open(hash.get("sheet") ?? this.index?.overview ?? "", hash);
  }

  fail(error: unknown): void {
    this.notice.textContent = error instanceof Error ? error.message : String(error);
    this.notice.hidden = false;
  }

  private go(work: Promise<void>): void {
    work.catch((error: unknown) => this.fail(error));
  }

  private get overview(): boolean {
    return this.sheet !== null && this.sheet.id === this.index?.overview;
  }

  private state(id: string | null): State | null {
    return this.states.states.find((found) => found.id === id) ?? null;
  }

  private async reload(): Promise<void> {
    this.renderStates();
    const shown = this.sheet?.id;
    await this.loadIndex();
    const sheets = this.changes?.sheets ?? this.index?.sheets ?? [];
    await this.open(shown !== undefined && sheets.some((found) => found.id === shown) ? shown : (this.index?.overview ?? ""));
  }

  private async loadIndex(): Promise<void> {
    const head = part(this.head);
    this.index = await fetchJson<SheetIndex>(`/api/states/${head}/sheets`);
    this.changes = this.base === null ? null : await fetchJson<ComparisonIndex>(`/api/compare/${part(this.base)}/${head}`);
  }

  private async open(sheetId: string, hash: URLSearchParams | null = null): Promise<void> {
    const head = part(this.head);
    const inHead = this.index?.sheets.some((found) => found.id === sheetId) ?? false;
    if (this.base === null) {
      this.comparison = null;
      this.sheet = await fetchJson<Sheet>(`/api/states/${head}/sheets/${part(sheetId)}`);
    } else {
      this.comparison = await fetchJson<SheetComparison>(`/api/compare/${part(this.base)}/${head}/sheets/${part(sheetId)}`);
      this.sheet = this.comparison.sheet;
    }
    const recorded = this.state(this.head)?.flows ?? false;
    this.flows = recorded && inHead ? await fetchJson<SheetFlows>(`/api/states/${head}/sheets/${part(sheetId)}/flows`) : NO_FLOWS;
    const sheet = this.sheet;
    this.expanded.clear();
    this.flow = null;
    this.placed = new Map();
    this.selection = null;
    this.view.select(null);
    if (hash !== null) {
      this.grouping = GROUPINGS.find((grouping) => grouping === hash.get("group")) ?? this.grouping;
      for (const title of (hash.get("expand") ?? "").split(",").filter(Boolean)) {
        const index = sheet.parts.findIndex((found) => found.title === title);
        if (index >= 0) {
          this.expanded.add(index);
        }
      }
      const wanted = hash.get("flow");
      this.flow = wanted === null ? null : (this.flows.flows.find((flow) => flow.test.includes(wanted)) ?? null);
    }
    this.renderSheets();
    this.renderCrumbs();
    this.renderControls();
    this.renderFlows();
    await this.relayout(true);
  }

  private async relayout(fit: boolean): Promise<void> {
    const sheet = this.sheet;
    if (sheet === null) {
      return;
    }
    const run = ++this.run;
    this.stage.classList.add("busy");
    const state: ViewState = { grouping: this.overview ? "none" : this.grouping, expanded: this.expanded, flow: this.flow };
    const d = drawing(sheet, state, this.flows.functions, this.comparison);
    const layout = await this.engine.arrange(elkGraph(d, state, this.placed));
    if (run !== this.run) {
      return;
    }
    this.stage.classList.remove("busy");
    this.viewport.attach(this.view.draw(d, layout, this.placed));
    if (fit) {
      this.viewport.fit(layout.width, layout.height);
    }
    this.placed = new Map([...layout.parts].map(([index, placed]) => [at(sheet.parts, index).id, { x: placed.x, y: placed.y }]));
    this.current = d;
    this.renderTitleBlock(d, layout.parts.size);
    this.renderPanel();
    this.writeHash();
  }

  private select(selection: Selection): void {
    this.selection = selection;
    this.view.select(selection);
    this.renderPanel();
  }

  private toggle(index: number): void {
    if (!this.expanded.delete(index)) {
      this.expanded.add(index);
    }
    void this.relayout(false);
  }

  private renderPanel(): void {
    const index = this.index;
    const sheet = this.sheet;
    const d = this.current;
    const head = this.state(this.head);
    if (index === null || sheet === null || d === null || head === null) {
      return;
    }
    const context: PanelContext = {
      index,
      sheet,
      drawing: d,
      flow: this.flow,
      flows: this.flows.flows.length,
      expanded: this.expanded,
      comparison: this.comparison,
      base: this.state(this.base),
      head,
    };
    const selection = this.selection;
    const asked = ++this.asked;
    if (selection !== null && "part" in selection) {
      this.inspector.showPart(context, selection.part, null, null);
      this.go(this.details(selection.part).then(([detail, before]) => {
        if (asked === this.asked) {
          this.inspector.showPart(context, selection.part, detail, before);
        }
      }));
      return;
    }
    const wire: Wire | undefined = selection !== null && "wire" in selection ? d.wires.find((w) => wireKey(w) === selection.wire) : undefined;
    if (wire !== undefined) {
      this.inspector.showWire(context, wire, null);
      this.go(this.carried(wire).then((data) => {
        if (asked === this.asked) {
          this.inspector.showWire(context, wire, data);
        }
      }));
      return;
    }
    if (this.flow !== null) {
      this.inspector.showFlow(context, this.flow);
      return;
    }
    this.inspector.showSheet(context);
  }

  /** The details of a part from the state that has it, and from the base state when it changed there. */
  private async details(index: number): Promise<[PartDetail, PartDetail | null]> {
    const id = part(at(this.sheet?.parts ?? [], index).id);
    const status = this.comparison?.parts[index]?.status ?? "same";
    const shownIn = status === "removed" && this.base !== null ? this.base : this.head;
    const detail = fetchJson<PartDetail>(`/api/states/${part(shownIn)}/parts/${id}`);
    const before = status === "changed" && this.base !== null ? fetchJson<PartDetail>(`/api/states/${part(this.base)}/parts/${id}`) : Promise.resolve(null);
    return Promise.all([detail, before]);
  }

  /** What each call on a wire carries, read from the state that has the call: the base state for a removed one. */
  private async carried(wire: Wire): Promise<WireData> {
    const parts = this.sheet?.parts ?? [];
    const requests = wire.calls.map((call, i) => ({
      index: i,
      call: call.target_method ? `${at(parts, call.target).id}|${call.target_method}` : null,
      state: wire.statuses[i] === "removed" && this.base !== null ? this.base : this.head,
    }));
    const signatures: WireData["signatures"][number][] = wire.calls.map(() => null);
    const types = new Map<string, CallData["types"][number]>();
    for (const state of new Set(requests.map((request) => request.state))) {
      const asked = requests.filter((request) => request.state === state && request.call !== null);
      if (asked.length === 0) {
        continue;
      }
      const query = asked.map((request) => `call=${encodeURIComponent(request.call ?? "")}`).join("&");
      const data = await fetchJson<CallData>(`/api/states/${part(state)}/data?${query}`);
      asked.forEach((request, n) => { signatures[request.index] = data.signatures[n] ?? null; });
      for (const found of data.types) {
        types.set(found.id, found);
      }
    }
    return { signatures, types: [...types.values()] };
  }

  private onPart(index: number): void {
    const found = this.sheet?.parts[index];
    if (found === undefined) {
      return;
    }
    if (found.kind === "package") {
      this.go(this.open(found.id));
      return;
    }
    this.selection = { part: index };
    this.view.select(this.selection);
    this.toggle(index);
  }

  private chooseFlow(flow: FlowPath | null): void {
    this.flow = flow;
    this.renderFlows();
    void this.relayout(false);
  }

  private renderStates(): void {
    const option = (value: string, label: string, title: string): HTMLOptionElement => {
      const found = html("option", label);
      found.value = value;
      found.title = title;
      return found;
    };
    this.headSelect.replaceChildren(...this.states.states.map((s) => option(s.id, s.label, s.subject)));
    this.headSelect.value = this.head;
    this.baseSelect.replaceChildren(
      option("", "none", ""),
      ...this.states.states.filter((s) => s.id !== this.head).map((s) => option(s.id, s.label, s.subject)),
    );
    this.baseSelect.value = this.base ?? "";
  }

  private renderSheets(): void {
    this.sheetList.textContent = "";
    const rows = this.changes?.sheets.map((found) => ({ id: found.id, title: found.title, count: found.changes ? String(found.changes) : "", status: found.status }))
      ?? (this.index?.sheets ?? []).map((found) => ({ id: found.id, title: found.title, count: String(found.parts), status: "same" }));
    for (const row of rows) {
      const button = html("button");
      button.setAttribute("aria-current", String(row.id === this.sheet?.id));
      button.append(html("span", row.title, `name ${row.status}`), html("span", row.count, `count ${row.status}`));
      button.addEventListener("click", () => this.go(this.open(row.id)));
      const item = html("li");
      item.append(button);
      this.sheetList.append(item);
    }
  }

  private renderCrumbs(): void {
    this.crumbs.textContent = "";
    const project = this.index?.project ?? "";
    const home = html("button", project, this.overview ? "here" : "");
    home.addEventListener("click", () => this.go(this.open(this.index?.overview ?? project)));
    this.crumbs.append(home);
    if (!this.overview && this.sheet !== null) {
      this.crumbs.append(html("span", "/", "sep"), html("span", this.sheet.title, "here"));
    }
  }

  private renderControls(): void {
    this.sheetControls.hidden = this.overview;
    for (const button of document.querySelectorAll<HTMLButtonElement>("[data-grouping]")) {
      button.setAttribute("aria-pressed", String(button.dataset["grouping"] === this.grouping));
    }
  }

  private renderFlows(): void {
    const query = this.filter.value.trim().toLowerCase();
    this.flowList.textContent = "";
    this.clearFlow.hidden = this.flow === null;
    const files = new Map<string, FlowPath[]>();
    for (const flow of this.flows.flows) {
      const file = (flow.test.split("::")[0] ?? flow.test).replace(/^tests\//, "");
      const name = flow.name === flow.test ? readableTest(flow.test).name : flow.name;
      if (query && !`${name} ${file}`.toLowerCase().includes(query)) {
        continue;
      }
      files.set(file, [...(files.get(file) ?? []), flow]);
    }
    for (const [file, flows] of [...files].sort(([a], [b]) => a.localeCompare(b))) {
      const details = html("details");
      details.open = query !== "" || flows.some((flow) => flow === this.flow);
      const summary = html("summary");
      const name = html("span", "", "file");
      const slash = file.lastIndexOf("/");
      name.append(html("span", file.slice(0, slash + 1), "folder"), html("span", file.slice(slash + 1).replace(/^test_/, "").replace(/\.py$/, ""), "stem"));
      name.title = file;
      summary.append(name, html("span", String(flows.length), "tally"));
      const list = html("ul");
      for (const flow of flows) {
        const words = flow.name === flow.test ? readableTest(flow.test) : { name: flow.name, param: "" };
        const button = html("button", words.name, flow.outcome);
        if (words.param) {
          button.append(html("span", ` ${words.param}`, "param"));
        }
        button.setAttribute("aria-pressed", String(flow === this.flow));
        button.addEventListener("click", () => this.chooseFlow(flow === this.flow ? null : flow));
        const item = html("li");
        item.append(button);
        list.append(item);
      }
      details.append(summary, list);
      this.flowList.append(details);
    }
    if (this.flows.flows.length === 0) {
      const recorded = this.state(this.head)?.flows ?? false;
      this.flowList.append(html("p", recorded ? "No flow runs on this sheet." : `No flows recorded for ${this.state(this.head)?.label ?? "this state"}.`, "empty"));
    }
  }

  /** The title block: what this sheet is, which states it shows, and what it holds. */
  private renderTitleBlock(d: Drawing, parts: number): void {
    const sheet = this.sheet;
    const head = this.state(this.head);
    if (sheet === null || head === null) {
      return;
    }
    const base = this.state(this.base);
    const sheets = this.index?.sheets ?? [];
    const number = Math.max(1, sheets.findIndex((found) => found.id === sheet.id) + 1);
    const changed = this.comparison === null ? 0 : [...this.comparison.parts.map((p) => p.status), ...this.comparison.calls].filter((s) => s !== "same").length;
    const flow = this.flow;
    const cell = (key: string, value: string, className = "", valueClass = ""): HTMLDivElement => {
      const found = html("div", "", `cell ${className}`);
      found.append(html("span", key, "key"), html("span", value, `value ${valueClass}`));
      return found;
    };
    const title = this.overview ? (this.index?.project ?? sheet.title) : `${this.index?.project ?? ""} / ${sheet.title}`;
    const rows: HTMLDivElement[][] = [
      [cell("Sheet", title, "double", "title"), cell("Sheet", `${number} of ${sheets.length}`)],
      base === null
        ? [cell("State", head.label), cell("Parts", String(parts)), cell("Wires", String(d.wires.length))]
        : [cell("State", head.label), cell("Compared with", base.label), cell("Changes", String(changed), "", changed ? "changed" : "")],
    ];
    if (flow !== null) {
      rows.push([cell("Flow", readableTest(flow.test).name, "double", "current"), cell("Calls", String(d.wires.reduce((sum, w) => sum + w.lit, 0)), "", "current")]);
    }
    rows.forEach((row, r) => {
      row.at(-1)?.classList.add("end");
      for (const found of row) {
        found.classList.toggle("last-row", r === rows.length - 1);
      }
    });
    this.titleBlock.replaceChildren(...rows.flat());
  }

  private writeHash(): void {
    const sheet = this.sheet;
    if (sheet === null) {
      return;
    }
    const params = new URLSearchParams({ sheet: sheet.id });
    if (this.head !== this.states.current) {
      params.set("head", this.head);
    }
    if (this.base !== null) {
      params.set("base", this.base);
    }
    if (this.grouping !== "none") {
      params.set("group", this.grouping);
    }
    if (this.expanded.size > 0) {
      params.set("expand", [...this.expanded].map((index) => at(sheet.parts, index).title).join(","));
    }
    if (this.flow !== null) {
      params.set("flow", this.flow.test);
    }
    history.replaceState(null, "", `#${params.toString()}`);
  }
}

const app = new App();
app.start().catch((error: unknown) => app.fail(error));
