export interface TestName {
  readonly name: string;
  readonly param: string;
}

export function readableTest(test: string): TestName {
  const name = test.split("::").pop() ?? test;
  const match = /^test_(.*?)(\[(.*)\])?$/.exec(name);
  if (match === null) {
    return { name, param: "" };
  }
  return { name: (match[1] ?? name).replace(/_/g, " "), param: (match[3] ?? "").replace(/_/g, " ") };
}

export function shortName(project: string, name: string): string {
  return name.startsWith(`${project}.`) ? name.slice(project.length + 1) : name;
}

export function relativePath(root: string, path: string): string {
  return path.startsWith(`${root}/`) ? path.slice(root.length + 1) : path;
}

/** A qualified name inside a type, such as `cdtwee.cm.commits.Change` in `tuple[cdtwee.cm.commits.Change, ...]`. */
export const QUALIFIED = /\b(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\b/g;

export function lastName(qualified: string): string {
  return qualified.slice(qualified.lastIndexOf(".") + 1);
}
