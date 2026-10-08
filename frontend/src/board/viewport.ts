import type { Graph } from "@antv/x6";

/** X6 writes inline dimensions; let flex measure first, then update its viewport. */
export function observeGraphSize(graph: Graph): () => void {
  const container = graph.container;
  let previousWidth = 0;
  let previousHeight = 0;
  const sync = () => {
    container.style.width = "";
    container.style.height = "";
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width <= 0 || height <= 0 || (width === previousWidth && height === previousHeight)) return;
    // A resize changes the viewport, not the user's zoom or node coordinates.
    // Keep the nearest visible card at the same relative position, fully inside.
    const scale = graph.scale();
    const translation = graph.translate();
    const candidates = previousWidth > 0 ? graph.getNodes().map(node => {
      const box = node.getBBox();
      const x = box.x + box.width / 2;
      const y = box.y + box.height / 2;
      return { box, x, y, screenX: x * scale.sx + translation.tx, screenY: y * scale.sy + translation.ty };
    }).filter(anchor => anchor.screenX >= 0 && anchor.screenX <= previousWidth
      && anchor.screenY >= 0 && anchor.screenY <= previousHeight) : [];
    candidates.sort((a, b) => Math.hypot(a.screenX - previousWidth / 2, a.screenY - previousHeight / 2)
      - Math.hypot(b.screenX - previousWidth / 2, b.screenY - previousHeight / 2));
    const anchor = candidates[0];
    graph.resize(width, height);
    if (anchor) {
      const nextScale = graph.scale();
      const clamp = (position: number, halfSize: number, extent: number) =>
        halfSize * 2 + 32 > extent ? extent / 2 : Math.max(halfSize + 16, Math.min(extent - halfSize - 16, position));
      const x = clamp(anchor.screenX / previousWidth * width, anchor.box.width * nextScale.sx / 2, width);
      const y = clamp(anchor.screenY / previousHeight * height, anchor.box.height * nextScale.sy / 2, height);
      graph.translate(x - anchor.x * nextScale.sx, y - anchor.y * nextScale.sy);
    }
    previousWidth = width;
    previousHeight = height;
  };
  sync();
  const observer = new ResizeObserver(sync);
  observer.observe(container);
  return () => observer.disconnect();
}
