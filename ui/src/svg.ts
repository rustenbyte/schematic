const NS = "http://www.w3.org/2000/svg";

type Attributes = Readonly<Record<string, string | number>>;

export function element<K extends keyof SVGElementTagNameMap>(name: K, attributes: Attributes, parent?: Element): SVGElementTagNameMap[K] {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  parent?.appendChild(node);
  return node;
}

export function text(parent: Element, x: number, y: number, value: string, className: string, extra: Attributes = {}): SVGTextElement {
  const node = element("text", { x, y, class: className, ...extra }, parent);
  node.textContent = value;
  return node;
}

export function path(sections: readonly (readonly { readonly x: number; readonly y: number }[])[]): string {
  return sections.map((section) => `M${section.map((p) => `${p.x} ${p.y}`).join("L")}`).join("");
}

/** A revision cloud: the scalloped outline a drawing puts round what changed, bulging outward on every side. */
export function cloud(x: number, y: number, width: number, height: number, radius: number): string {
  const across = Math.max(1, Math.round(width / (2 * radius)));
  const down = Math.max(1, Math.round(height / (2 * radius)));
  const step = width / across;
  const drop = height / down;
  const arcs = (count: number, size: number, dx: number, dy: number): string =>
    Array.from({ length: count }, () => `a${size / 2} ${size / 2} 0 0 1 ${dx} ${dy}`).join("");
  return `M${x} ${y}${arcs(across, step, step, 0)}${arcs(down, drop, 0, drop)}${arcs(across, step, -step, 0)}${arcs(down, drop, 0, -drop)}Z`;
}
