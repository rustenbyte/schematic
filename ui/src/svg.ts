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
