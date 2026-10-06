import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const assetRoot = resolve("public/mascot/live2d");

describe("Live2D mascot assets", () => {
  it("ships a model manifest whose referenced model exists and excludes Cubism Core", () => {
    const manifest = JSON.parse(readFileSync(join(assetRoot, "pet.json"), "utf8"));
    const relativeModel = manifest.live2d.model as string;
    expect(relativeModel).toMatch(/^[A-Za-z0-9._/-]+\.model3\.json$/);
    const modelPath = resolve(assetRoot, relativeModel);
    // 路径分隔符无关的逃逸检查（Windows 的 resolve 返回反斜杠，不能用 `${assetRoot}/` 前缀断言）
    expect(relative(assetRoot, modelPath).startsWith("..")).toBe(false);
    expect(existsSync(modelPath)).toBe(true);

    const files: string[] = [];
    const visit = (directory: string) => {
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) visit(path);
        else files.push(path);
      }
    };
    visit(assetRoot);
    expect(files.some((path) => /live2dcubismcore\.min\.js$/i.test(path))).toBe(false);
  });
});
