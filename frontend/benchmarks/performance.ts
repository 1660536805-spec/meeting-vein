import { createGraph, focusNode, renderBoard } from "../src/board/render";
import { serializeBoardSvg } from "../src/board/export";

const result = document.querySelector<HTMLElement>("#results")!;
const mount = document.querySelector<HTMLElement>("#bench-board")!;
const sizes = [10, 200, 500];
const reloadKey = "huimai-perf-reload-queue";
const reloadResultKey = "huimai-perf-reload-results";
let graph: ReturnType<typeof createGraph> | null = null;

function syntheticCells(sentenceCount: number): any[] {
  const branchCount = Math.min(5, sentenceCount);
  return Array.from({ length: 1 + branchCount + sentenceCount }, (_, index) => {
    const id = index === 0 ? "n_issue_root" : `n_point_${index}`;
    const isBranch = index > 0 && index <= branchCount;
    const utteranceIndex = index - branchCount;
    const branchIndex = Math.min(branchCount - 1, Math.floor((utteranceIndex - 1) / Math.max(1, Math.ceil(sentenceCount / branchCount))));
    const parentId = index === 0 ? null : isBranch ? "n_issue_root" : `n_point_${branchIndex + 1}`;
    const cell = {
      id,
      shape: "rect",
      data: {
        type: index === 0 || isBranch ? "issue" : index % 17 === 0 ? "conclusion" : "point",
        label: index === 0 ? "合成讨论议题\n性能验证样本" : isBranch ? `讨论分支 ${index}` : `第 ${utteranceIndex} 条发言：讨论方案是否可行，需要结合成本、时间与用户反馈逐步确认。`,
        parent_id: parentId,
        sentence_id: isBranch || index === 0 ? undefined : `synthetic-${utteranceIndex}`,
        confidence: 0.9,
      },
    };
    if (index === 0) return cell;
    return [cell, {
      id: `e_${index}`,
      shape: "edge",
      source: { cell: parentId },
      target: { cell: id },
      data: { relation: "subordinate" },
    }];
  }).flat();
}

const afterPaint = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const median = (samples: number[]) => [...samples].sort((a, b) => a - b)[Math.floor(samples.length / 2)];

async function renderSample(sentenceCount: number) {
  graph?.dispose();
  mount.replaceChildren();
  graph = createGraph(mount, true);
  const cells = syntheticCells(sentenceCount);
  const start = performance.now();
  renderBoard(graph, cells);
  await afterPaint();
  const firstScreen = performance.now() - start;

  const focusTarget = `n_point_${branchCountFor(sentenceCount) + Math.ceil(sentenceCount / 2)}`;
  const focusTimes: number[] = [];
  for (let run = 0; run < 5; run++) {
    const focusStart = performance.now();
    focusNode(graph, focusTarget);
    focusTimes.push(performance.now() - focusStart);
  }
  // X6 centerCell animates the viewport. Let the far pan in the 500-node case settle
  // before measuring the currently visible SVG export.
  await new Promise((resolve) => window.setTimeout(resolve, 350));
  const exportTimes: number[] = [];
  for (let run = 0; run < 5; run++) {
    const exportStart = performance.now();
    serializeBoardSvg(graph);
    exportTimes.push(performance.now() - exportStart);
  }
  const serialized = serializeBoardSvg(graph).xml;
  const serializedNodes = serialized.match(/x6-node/g)?.length ?? 0;
  return {
    sentences: sentenceCount,
    graphNodes: graph.getNodes().length,
    firstScreenMs: Math.round(firstScreen),
    localFocusMedianMs: Number(median(focusTimes).toFixed(2)),
    svgExportMedianMs: Number(median(exportTimes).toFixed(2)),
    svgBytes: new Blob([serialized]).size,
    serializedNodes,
  };
}

function branchCountFor(sentenceCount: number) { return Math.min(5, sentenceCount); }

async function runSuite() {
  result.textContent = "运行合成样本……";
  const measurements = [];
  for (const size of sizes) measurements.push(await renderSample(size));
  result.textContent = [
    "本地合成样本；毫秒；首次绘制包含两帧等待；焦点和 SVG 为 5 次中位数。",
    JSON.stringify({ environment: navigator.userAgent, viewport: `${innerWidth}×${innerHeight}`, measurements }, null, 2),
  ].join("\n\n");
  sessionStorage.setItem("huimai-perf-last-results", JSON.stringify(measurements));
}

async function runReloadQueue() {
  const rawQueue = sessionStorage.getItem(reloadKey);
  if (!rawQueue) return;
  const queue = JSON.parse(rawQueue) as number[];
  const previous = JSON.parse(sessionStorage.getItem(reloadResultKey) ?? "[]") as any[];
  const size = queue.shift();
  if (size === undefined) return;
  result.textContent = `刷新后恢复 ${size} 句合成样本……`;
  await renderSample(size);
  await afterPaint();
  previous.push({ sentences: size, browserRefreshToPaintMs: Math.round(performance.now() + performance.timeOrigin - Number(sessionStorage.getItem("huimai-perf-reload-start"))) });
  sessionStorage.setItem(reloadResultKey, JSON.stringify(previous));
  if (queue.length) {
    sessionStorage.setItem(reloadKey, JSON.stringify(queue));
    sessionStorage.setItem("huimai-perf-reload-start", String(Date.now()));
    location.reload();
    return;
  }
  sessionStorage.removeItem(reloadKey);
  sessionStorage.removeItem("huimai-perf-reload-start");
  sessionStorage.removeItem(reloadResultKey);
  result.textContent = `浏览器完整刷新至合成图谱可见（含网络加载、JS 初始化、渲染和两帧等待），毫秒：\n${JSON.stringify(previous, null, 2)}\n\n样本为合成中文节点。`;
}

document.querySelector("#run")!.addEventListener("click", () => { void runSuite(); });
document.querySelector("#reload")!.addEventListener("click", () => {
  sessionStorage.setItem(reloadKey, JSON.stringify(sizes));
  sessionStorage.setItem(reloadResultKey, "[]");
  sessionStorage.setItem("huimai-perf-reload-start", String(Date.now()));
  location.reload();
});
void runReloadQueue();
