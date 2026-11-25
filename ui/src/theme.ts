export type ThemeName = "paper" | "blueprint";

const THEMES: readonly ThemeName[] = ["paper", "blueprint"];
const KEY = "schematic.theme";

/** The look of the page: paper, or blueprint for dark surroundings; a choice is kept in this browser only. */
export class Theme {
  private readonly root: HTMLElement;
  private readonly buttons: readonly HTMLButtonElement[];

  constructor(root: HTMLElement, buttons: readonly HTMLButtonElement[]) {
    this.root = root;
    this.buttons = buttons;
    for (const button of buttons) {
      button.addEventListener("click", () => this.choose(THEMES.find((name) => name === button.dataset["themeChoice"]) ?? "paper"));
    }
    this.apply(this.saved() ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "blueprint" : "paper"));
  }

  private choose(name: ThemeName): void {
    this.apply(name);
    try {
      localStorage.setItem(KEY, name);
    } catch {
      // A browser that blocks storage keeps the choice for this page only.
    }
  }

  private saved(): ThemeName | undefined {
    try {
      const stored = localStorage.getItem(KEY);
      return THEMES.find((name) => name === stored);
    } catch {
      return undefined;
    }
  }

  private apply(name: ThemeName): void {
    this.root.dataset["theme"] = name;
    for (const button of this.buttons) {
      button.setAttribute("aria-pressed", String(button.dataset["themeChoice"] === name));
    }
  }
}
