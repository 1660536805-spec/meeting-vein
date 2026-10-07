import type { Graph } from "@antv/x6";

export function serializeBoardSvg(graph: Graph): { xml: string; width: number; height: number } {
  const source = graph.container.querySelector("svg.x6-graph-svg") || graph.container.querySelector("svg");
  if (!(source instanceof SVGSVGElement)) throw new Error("看板尚未准备好");
  const width = Math.max(1, graph.container.clientWidth);
  const height = Math.max(1, graph.container.clientHeight);
  const copy = source.cloneNode(true) as SVGSVGElement;
  copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  copy.setAttribute("width", String(width));
  copy.setAttribute("height", String(height));
  const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  background.setAttribute("width", "100%");
  background.setAttribute("height", "100%");
  background.setAttribute("fill", document.documentElement.dataset.theme === "light" ? "#F8FCFF" : "#0b1b2e");
  copy.insertBefore(background, copy.firstChild);
  return { xml: new XMLSerializer().serializeToString(copy), width, height };
}

function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadSVG(graph: Graph): void {
  const { xml } = serializeBoardSvg(graph);
  download(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }), "meeting-board.svg");
}

export async function downloadPNG(graph: Graph): Promise<void> {
  const { xml, width, height } = serializeBoardSvg(graph);
  const url = URL.createObjectURL(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = width * 2;
    canvas.height = height * 2;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法生成 PNG");
    context.scale(2, 2);
    context.drawImage(image, 0, 0, width, height);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((result) => result ? resolve(result) : reject(new Error("PNG 导出失败")), "image/png"));
    download(blob, "meeting-board.png");
  } finally { URL.revokeObjectURL(url); }
}
