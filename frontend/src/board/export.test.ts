import { describe, expect, it } from "vitest";
import { serializeBoardSvg } from "./export";

describe("serializeBoardSvg", () => {
  it("clones the visible graph with explicit SVG dimensions and a background", () => {
    const container = document.createElement("div");
    Object.defineProperties(container, {
      clientWidth: { configurable: true, value: 640 },
      clientHeight: { configurable: true, value: 360 },
    });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("x6-graph-svg");
    svg.setAttribute("viewBox", "0 0 640 360");
    container.append(svg);

    const result = serializeBoardSvg({ container } as any);

    expect(result).toMatchObject({ width: 640, height: 360 });
    expect(result.xml).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(result.xml).toContain('width="640"');
    expect(result.xml).toContain('height="360"');
    expect(result.xml).toContain("<rect width=\"100%\" height=\"100%\"");
  });
});
