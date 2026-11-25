export type PartKind = "class" | "module" | "package";
export type Role = "service" | "internal" | "module" | "package";

export interface Part {
  readonly id: string;
  readonly title: string;
  readonly kind: PartKind;
  readonly role: Role;
  readonly file: string;
  readonly path: string;
  readonly service: string;
  readonly methods: readonly string[];
}

export interface Call {
  readonly source: number;
  readonly source_method: string;
  readonly target: number;
  readonly target_method: string;
  readonly count: number;
  readonly lines: readonly number[];
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
  readonly root: string;
  readonly overview: string;
  readonly sheets: readonly SheetSummary[];
}

export interface Collaborator {
  readonly name: string;
  readonly type: string;
  readonly parts: readonly string[];
}

export interface Member {
  readonly name: string;
  readonly line: number;
  readonly doc: string | null;
}

export interface PartDetail {
  readonly id: string;
  readonly title: string;
  readonly kind: PartKind;
  readonly role: Role;
  readonly path: string;
  readonly line: number | null;
  readonly doc: string | null;
  readonly kinds: readonly string[];
  readonly implements: readonly string[];
  readonly built_with: readonly Collaborator[];
  readonly held_by: readonly string[];
  readonly members: readonly Member[];
  readonly raises: readonly string[];
  readonly types: readonly string[];
}

export interface FunctionRef {
  readonly sheet: string;
  readonly part: string;
  readonly method: string;
}

export type Status = "same" | "added" | "removed" | "changed";

export interface State {
  readonly id: string;
  readonly label: string;
  readonly subject: string;
  readonly date: string;
  readonly flows: boolean;
}

export interface StateList {
  readonly current: string;
  readonly states: readonly State[];
}

export interface PartChange {
  readonly status: Status;
  readonly methods: Readonly<Record<string, Status>>;
  readonly doc: boolean;
  readonly changes: number;
}

export interface SheetComparison {
  readonly base: string;
  readonly head: string;
  readonly sheet: Sheet;
  readonly parts: readonly PartChange[];
  readonly calls: readonly Status[];
  readonly inbound: readonly Status[];
  readonly outbound: readonly Status[];
}

export interface SheetChanges {
  readonly id: string;
  readonly title: string;
  readonly status: Status;
  readonly changes: number;
}

export interface ComparisonIndex {
  readonly base: string;
  readonly head: string;
  readonly overview: string;
  readonly sheets: readonly SheetChanges[];
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
