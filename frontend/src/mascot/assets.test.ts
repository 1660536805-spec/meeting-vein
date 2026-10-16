import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("mascot portrait assets", () => {
  it("uses the current assistant on every home entry and removes old cover layers", () => {
    for (const route of ["index.html", "home.html", "intro.html"]) {
      const html = readFileSync(route, "utf8");
      expect(html).toContain("/huimai-home-clean-light.png");
      expect(html).toContain("/huimai-home-clean-dark.png");
      expect(html).not.toContain("cover-tail");
      expect(html).not.toContain("huimai-home-hero-");
    }
    const css = readFileSync("src/cover-motion.css", "utf8");
    expect(css).toContain("/mascot/assistant.png");
    expect(css).not.toContain("huimai-home-hero-");
    expect(existsSync("public/huimai-home-clean-light.png")).toBe(true);
    expect(existsSync("public/huimai-home-clean-dark.png")).toBe(true);
  });
  it("uses the new local portrait and no longer ships the old model", () => {
    const html = readFileSync("workspace.html", "utf8");
    expect(html).toContain('/mascot/assistant.png');
    expect(html).not.toContain('data-fallback-src');
    expect(existsSync("public/mascot/assistant.png")).toBe(true);
    expect(existsSync("public/mascot/live2d")).toBe(false);
    expect(existsSync("public/mascot/fallback.svg")).toBe(false);
  });
});
