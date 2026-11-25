export type PartKind = "class" | "module" | "package";
export type Role = "service" | "internal" | "module" | "package";

export interface Part {
  readonly id: string;
  readonly title: string;
  readonly kind: PartKind;
  readonly role: Role;
  readonly file: string;
  readonly service: string;
  readonly methods: readonly string[];
}

export interface Call {
  readonly source: number;
  readonly source_method: string;
  readonly target: number;
  readonly target_method: string;
  readonly count: number;
}

export interface Inbound {
  readonly sheet: string;
  readonly target: number;
  readonly target_method: string;
  readonly count: number;
}

export interface Outbound {
  readonly source: number;
  readonly source_method: string;
  readonly target: string;
  readonly count: number;
}

export interface Sheet {
  readonly id: string;
  readonly title: string;
  readonly parts: readonly Part[];
  readonly calls: readonly Call[];
  readonly inbound: readonly Inbound[];
  readonly outbound: readonly Outbound[];
}

export interface SheetSummary {
  readonly id: string;
  readonly title: string;
  readonly parts: number;
}

export interface SheetIndex {
  readonly project: string;
  readonly overview: string;
  readonly sheets: readonly SheetSummary[];
}

export interface FunctionRef {
  readonly sheet: string;
  readonly part: number;
  readonly method: string;
}

export interface FlowPath {
  readonly test: string;
  readonly name: string;
  readonly outcome: string;
  readonly edges: readonly (readonly [number, number, number])[];
  readonly raises: readonly (readonly [number, string, boolean])[];
}

export interface SheetFlows {
  readonly functions: readonly FunctionRef[];
  readonly flows: readonly FlowPath[];
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} answered ${response.status}: ${await response.text()}`);
  }
  // The server writes these shapes from its Pydantic models; this is the one place they enter untyped.
  const body: T = await response.json();
  return body;
}
