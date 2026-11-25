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
