export type PanelName = "rail" | "side";

const NAMES: readonly PanelName[] = ["rail", "side"];
const LIMITS: Readonly<Record<PanelName, { readonly min: number; readonly max: number }>> = {
  rail: { min: 180, max: 520 },
  side: { min: 260, max: 720 },
};
const STAGE_MIN = 320;
const STEP = 16;

export interface PanelEvents {
  /** A panel's inner edge moved by `dx` pixels to the right, by opening, closing or resizing. */
  readonly moved: (name: PanelName, dx: number) => void;
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch {
    // A browser that blocks storage keeps the choice for this page only.
  }
}

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The two side panels: each opens, closes and takes the width it is dragged to, and stays so in this browser. */
export class Panels {
  private readonly body: HTMLElement;
  private readonly buttons: Readonly<Record<PanelName, HTMLButtonElement>>;
  private readonly panels: Readonly<Record<PanelName, HTMLElement>>;
  private readonly handles: Readonly<Record<PanelName, HTMLElement>>;
  private readonly events: PanelEvents;

  constructor(
    body: HTMLElement,
    parts: {
      readonly buttons: Readonly<Record<PanelName, HTMLButtonElement>>;
      readonly panels: Readonly<Record<PanelName, HTMLElement>>;
      readonly handles: Readonly<Record<PanelName, HTMLElement>>;
    },
    events: PanelEvents,
  ) {
    this.body = body;
    this.buttons = parts.buttons;
    this.panels = parts.panels;
    this.handles = parts.handles;
    this.events = events;
    for (const name of NAMES) {
      this.buttons[name].addEventListener("click", () => this.toggle(name));
      this.listen(name);
      const width = Number(stored(`schematic.${name}.width`));
      if (width > 0) {
        this.setWidth(name, width);
      }
      this.apply(name, stored(`schematic.${name}`) !== "closed");
    }
  }

  toggle(name: PanelName): void {
    const open = !this.isOpen(name);
    const before = this.width(name);
    this.apply(name, open);
    this.events.moved(name, open ? this.width(name) : -before);
    store(`schematic.${name}`, open ? "open" : "closed");
  }

  isOpen(name: PanelName): boolean {
    return !this.body.classList.contains(`${name}-closed`);
  }

  private width(name: PanelName): number {
    return this.panels[name].getBoundingClientRect().width;
  }

  private listen(name: PanelName): void {
    const handle = this.handles[name];
    const sign = name === "rail" ? 1 : -1;
    let start: { x: number; width: number } | null = null;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      start = { x: event.clientX, width: this.width(name) };
      handle.setPointerCapture(event.pointerId);
      this.body.classList.add("resizing");
    });
    handle.addEventListener("pointermove", (event) => {
      if (start !== null) {
        this.resize(name, start.width + sign * (event.clientX - start.x));
      }
    });
    const stop = (): void => {
      if (start === null) {
        return;
      }
      start = null;
      this.body.classList.remove("resizing");
      store(`schematic.${name}.width`, String(Math.round(this.width(name))));
    };
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
    handle.addEventListener("dblclick", () => {
      const before = this.width(name);
      this.body.style.removeProperty(`--${name}-width`);
      store(`schematic.${name}.width`, null);
      this.moved(name, before);
    });
    handle.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? STEP * 4 : STEP;
      const change = { ArrowLeft: -step, ArrowRight: step }[event.key];
      if (change === undefined) {
        return;
      }
      event.preventDefault();
      this.resize(name, this.width(name) + sign * change);
      store(`schematic.${name}.width`, String(Math.round(this.width(name))));
    });
  }

  private resize(name: PanelName, wanted: number): void {
    const before = this.width(name);
    this.setWidth(name, wanted);
    this.moved(name, before);
  }

  private setWidth(name: PanelName, wanted: number): void {
    const other = NAMES.find((found) => found !== name) ?? name;
    const otherWidth = this.isOpen(other) ? this.width(other) : 0;
    const max = Math.min(LIMITS[name].max, window.innerWidth - otherWidth - STAGE_MIN);
    const width = Math.round(Math.max(LIMITS[name].min, Math.min(max, wanted)));
    this.body.style.setProperty(`--${name}-width`, `${width}px`);
    this.handles[name].setAttribute("aria-valuenow", String(width));
  }

  private moved(name: PanelName, before: number): void {
    if (name === "rail") {
      this.events.moved(name, this.width(name) - before);
    }
  }

  private apply(name: PanelName, open: boolean): void {
    this.body.classList.toggle(`${name}-closed`, !open);
    this.panels[name].hidden = !open;
    this.handles[name].hidden = !open;
    this.buttons[name].setAttribute("aria-expanded", String(open));
    this.handles[name].setAttribute("aria-valuemin", String(LIMITS[name].min));
    this.handles[name].setAttribute("aria-valuemax", String(LIMITS[name].max));
    this.handles[name].setAttribute("aria-valuenow", String(Math.round(this.width(name))));
  }
}
