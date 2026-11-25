import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk-api.js";
import type { Graph, Point } from "./model.ts";

export interface Box {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
}

export interface PlacedPart extends Box {
  readonly ports: ReadonlyMap<string, Point>;
}

export interface PlacedGroup extends Box {
  readonly title: string;
}

export interface Route {
  readonly sections: readonly (readonly Point[])[];
  readonly junctions: readonly Point[];
}

export interface Layout {
  readonly width: number;
  readonly height: number;
  readonly parts: ReadonlyMap<number, PlacedPart>;
  readonly groups: ReadonlyMap<string, PlacedGroup>;
  readonly routes: ReadonlyMap<string, Route>;
}

/** Runs ELK on a graph and turns its nested, relative result into absolute positions. */
export class LayoutEngine {
  private readonly elk = new ELK();

  async arrange(graph: Graph): Promise<Layout> {
    const laid = await this.elk.layout(graph.root);
    const parts = new Map<number, PlacedPart>();
    const groups = new Map<string, PlacedGroup>();
    const place = (node: ElkNode, ox: number, oy: number): void => {
      for (const child of node.children ?? []) {
        const x = ox + (child.x ?? 0);
        const y = oy + (child.y ?? 0);
        const box = { x, y, w: child.width ?? 0, h: child.height ?? 0 };
        if (child.id.startsWith("g:")) {
          groups.set(child.id, { ...box, title: graph.titles.get(child.id) ?? "" });
          place(child, x, y);
          continue;
        }
        const ports = new Map((child.ports ?? []).map((port) => [port.id, { x: port.x ?? 0, y: port.y ?? 0 }]));
        parts.set(Number(child.id.slice(1)), { ...box, ports });
      }
    };
    place(laid, 0, 0);
    const routes = new Map<string, Route>();
    for (const edge of laid.edges ?? []) {
      routes.set(edge.id, {
        sections: (edge.sections ?? []).map((section) => [section.startPoint, ...(section.bendPoints ?? []), section.endPoint]),
        junctions: edge.junctionPoints ?? [],
      });
    }
    return { width: laid.width ?? 0, height: laid.height ?? 0, parts, groups, routes };
  }
}
