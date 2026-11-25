import type { FlowPath, Sheet, SheetFlows, SheetIndex } from "./api.ts";
import { fetchJson } from "./api.ts";
import { LayoutEngine } from "./layout.ts";
import type { Drawing, Grouping, Point, ViewState } from "./model.ts";
import { at, drawing, elkGraph } from "./model.ts";
import { SheetView } from "./view.ts";
import { Viewport } from "./viewport.ts";

const GROUPINGS: readonly Grouping[] = ["none", "role", "file", "service"];

function byId<T extends Element>(id: string, kind: { new (): T; prototype: T }): T {
  const found = document.getElementById(id);
  if (!(found instanceof kind)) {
    throw new Error(`the page has no #${id}`);
  }
  return found;
}

function readable(test: string): { name: string; param: string } {
  const name = test.split("::").pop() ?? test;
  const match = /^test_(.*?)(\[(.*)\])?$/.exec(name);
  if (match === null) {
    return { name, param: "" };
  }
  return { name: (match[1] ?? name).replace(/_/g, " "), param: (match[3] ?? "").replace(/_/g, " ") };
}

function html<K extends keyof HTMLElementTagNameMap>(name: K, text = "", className = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(name);
  node.textContent = text;
  if (className) {
    node.className = className;
  }
  return node;
}

/** The page: picks the sheet, the flow, the grouping and the expanded parts, and has the sheet laid out and drawn. */
class App {
  private readonly svg = byId("drawing", SVGSVGElement);
  private readonly stage = byId("stage", HTMLElement);
  private readonly sheetList = byId("sheets", HTMLOListElement);
  private readonly flowList = byId("flows", HTMLDivElement);
  private readonly filter = byId("filter", HTMLInputElement);
  private readonly crumbs = byId("crumbs", HTMLElement);
  private readonly figures = byId("figures", HTMLDivElement);
  private readonly sheetControls = byId("sheet-controls", HTMLDivElement);
  private readonly clearFlow = byId("clear-flow", HTMLButtonElement);
  private readonly notice = byId("notice", HTMLParagraphElement);
  private readonly engine = new LayoutEngine();
  private readonly viewport = new Viewport(this.svg, this.stage);
  private readonly view = new SheetView(this.svg, { part: (index) => this.onPart(index), dragging: () => this.viewport.dragging });
  private readonly expanded = new Set<number>();
  private index: SheetIndex | null = null;
  private sheet: Sheet | null = null;
  private flows: SheetFlows = { functions: [], flows: [] };
  private grouping: Grouping = "none";
  private flow: FlowPath | null = null;
  private placed = new Map<string, Point>();
  private run = 0;

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
  }

  async start(): Promise<void> {
    this.index = await fetchJson<SheetIndex>("/api/sheets");
    const hash = new URLSearchParams(location.hash.slice(1));
    await this.open(hash.get("sheet") ?? this.index.overview, hash);
  }

  fail(error: unknown): void {
    this.notice.textContent = error instanceof Error ? error.message : String(error);
    this.notice.hidden = false;
  }

  private get overview(): boolean {
    return this.sheet !== null && this.sheet.id === this.index?.overview;
  }

  private async open(sheetId: string, hash: URLSearchParams | null = null): Promise<void> {
    const path = `/api/sheets/${encodeURIComponent(sheetId)}`;
    const [sheet, flows] = await Promise.all([fetchJson<Sheet>(path), fetchJson<SheetFlows>(`${path}/flows`)]);
    this.sheet = sheet;
    this.flows = flows;
    this.expanded.clear();
    this.flow = null;
    this.placed = new Map();
    if (hash !== null) {
      this.grouping = GROUPINGS.find((grouping) => grouping === hash.get("group")) ?? this.grouping;
      for (const title of (hash.get("expand") ?? "").split(",").filter(Boolean)) {
        const index = sheet.parts.findIndex((part) => part.title === title);
        if (index >= 0) {
          this.expanded.add(index);
        }
      }
      const wanted = hash.get("flow");
      this.flow = wanted === null ? null : flows.flows.find((flow) => flow.test.includes(wanted)) ?? null;
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
    this.svg.classList.add("busy");
    const state: ViewState = { grouping: this.overview ? "none" : this.grouping, expanded: this.expanded, flow: this.flow };
    const d = drawing(sheet, state, this.flows.functions);
    const started = performance.now();
    const layout = await this.engine.arrange(elkGraph(d, state, this.placed));
    if (run !== this.run) {
      return;
    }
    this.svg.classList.remove("busy");
    this.viewport.attach(this.view.draw(d, layout, this.placed));
    if (fit) {
      this.viewport.fit(layout.width, layout.height);
    }
    this.placed = new Map([...layout.parts].map(([index, placed]) => [at(sheet.parts, index).id, { x: placed.x, y: placed.y }]));
    this.renderFigures(d, layout.parts.size, Math.round(performance.now() - started));
    this.writeHash();
  }

  private onPart(index: number): void {
    const part = this.sheet === null ? undefined : this.sheet.parts[index];
    if (part === undefined) {
      return;
    }
    if (part.kind === "package") {
      void this.open(part.id).catch((error: unknown) => this.fail(error));
      return;
    }
    if (!this.expanded.delete(index)) {
      this.expanded.add(index);
    }
    void this.relayout(false);
  }

  private chooseFlow(flow: FlowPath | null): void {
    this.flow = flow;
    this.renderFlows();
    void this.relayout(false);
  }

  private renderSheets(): void {
    this.sheetList.textContent = "";
    for (const summary of this.index?.sheets ?? []) {
      const button = html("button");
      button.setAttribute("aria-current", String(summary.id === this.sheet?.id));
      button.append(html("span", summary.title), html("span", String(summary.parts), "count"));
      button.addEventListener("click", () => void this.open(summary.id).catch((error: unknown) => this.fail(error)));
      const item = html("li");
      item.append(button);
      this.sheetList.append(item);
    }
  }

  private renderCrumbs(): void {
    this.crumbs.textContent = "";
    const project = this.index?.project ?? "";
    const home = html("button", project, this.overview ? "here" : "");
    home.addEventListener("click", () => void this.open(this.index?.overview ?? project).catch((error: unknown) => this.fail(error)));
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
      const name = flow.name === flow.test ? readable(flow.test).name : flow.name;
      if (query && !`${name} ${file}`.toLowerCase().includes(query)) {
        continue;
      }
      files.set(file, [...(files.get(file) ?? []), flow]);
    }
    for (const [file, flows] of [...files].sort(([a], [b]) => a.localeCompare(b))) {
      const details = html("details");
      details.open = query !== "" || flows.some((flow) => flow === this.flow);
      const summary = html("summary");
      summary.append(html("span", file), html("span", String(flows.length)));
      const list = html("ul");
      for (const flow of flows) {
        const words = flow.name === flow.test ? readable(flow.test) : { name: flow.name, param: "" };
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
      this.flowList.append(html("p", "No flows recorded: run schematic record.", "empty"));
    }
  }

  private renderFigures(d: Drawing, parts: number, milliseconds: number): void {
    this.figures.textContent = "";
    const rows: [string, string][] = [
      [String(parts), "parts"],
      [String(d.wires.length), "wires"],
      [String(this.expanded.size), "expanded"],
      [`${milliseconds} ms`, "layout"],
    ];
    for (const [value, label] of rows) {
      const span = html("span");
      span.append(html("b", value), ` ${label}`);
      this.figures.append(span);
    }
  }

  private writeHash(): void {
    const sheet = this.sheet;
    if (sheet === null) {
      return;
    }
    const params = new URLSearchParams({ sheet: sheet.id });
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
