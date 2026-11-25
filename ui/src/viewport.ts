/** Pan and zoom of the drawing: drag to move, wheel or pinch to zoom at the pointer. */
export class Viewport {
  private readonly svg: SVGSVGElement;
  private readonly stage: HTMLElement;
  private target: SVGGElement | null = null;
  private x = 0;
  private y = 0;
  private k = 1;
  private drag: { x: number; y: number; vx: number; vy: number; moved: boolean } | null = null;
  private dragged = false;

  constructor(svg: SVGSVGElement, stage: HTMLElement) {
    this.svg = svg;
    this.stage = stage;
    svg.addEventListener("pointerdown", (event) => {
      if (event.button === 0) {
        this.dragged = false;
        this.drag = { x: event.clientX, y: event.clientY, vx: this.x, vy: this.y, moved: false };
      }
    });
    svg.addEventListener("pointermove", (event) => this.move(event));
    svg.addEventListener("pointerup", () => {
      this.drag = null;
      svg.classList.remove("dragging");
      setTimeout(() => { this.dragged = false; }, 0);
    });
    svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      const box = stage.getBoundingClientRect();
      this.zoomAt(Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0018)), event.clientX - box.left, event.clientY - box.top);
    }, { passive: false });
  }

  get dragging(): boolean {
    return this.dragged;
  }

  attach(target: SVGGElement): void {
    this.target = target;
    this.apply();
  }

  /** Fit the sheet into the stage, clear of the toolbars above it and the title block below it. */
  fit(width: number, height: number): void {
    const box = this.stage.getBoundingClientRect();
    const room = { top: 64, right: 18, bottom: 96, left: 18 };
    const across = box.width - room.left - room.right;
    const down = box.height - room.top - room.bottom;
    this.k = Math.max(0.05, Math.min(across / width, down / height, 1.2));
    this.x = room.left + (across - width * this.k) / 2;
    this.y = room.top + (down - height * this.k) / 2;
    this.apply();
  }

  /** Move the drawing on screen, as when the stage's left edge moves and the drawing should stay where it was. */
  pan(dx: number): void {
    this.x += dx;
    this.apply();
  }

  zoomBy(factor: number): void {
    const box = this.stage.getBoundingClientRect();
    this.zoomAt(factor, box.width / 2, box.height / 2);
  }

  private zoomAt(factor: number, cx: number, cy: number): void {
    const k = Math.max(0.08, Math.min(4, this.k * factor));
    const ratio = k / this.k;
    this.x = cx - (cx - this.x) * ratio;
    this.y = cy - (cy - this.y) * ratio;
    this.k = k;
    this.apply();
  }

  private move(event: PointerEvent): void {
    const drag = this.drag;
    if (drag === null) {
      return;
    }
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 3) {
      drag.moved = true;
      this.dragged = true;
      this.svg.classList.add("dragging");
      this.svg.setPointerCapture(event.pointerId);
    }
    if (drag.moved) {
      this.x = drag.vx + dx;
      this.y = drag.vy + dy;
      this.apply();
    }
  }

  private apply(): void {
    this.target?.setAttribute("transform", `translate(${this.x},${this.y}) scale(${this.k})`);
  }
}
