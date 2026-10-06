import { createGraph, renderBoard } from "./board/render";
import "@antv/x6/dist/index.css";

const title = document.getElementById("snapshot-title")!;
const meta = document.getElementById("snapshot-meta")!;
const error = document.getElementById("snapshot-error")!;
const token = new URLSearchParams(location.search).get("token");

if (!token) {
  title.textContent = "缺少快照链接";
  error.textContent = "请从会议看板重新生成分享快照。";
  error.hidden = false;
} else {
  void fetch(`/api/view/${encodeURIComponent(token)}`)
    .then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(({ doc }) => {
      title.textContent = doc.title || "会议快照";
      meta.textContent = `版本 v${doc.version} · ${doc.created_at || ""}`;
      const graph = createGraph(document.getElementById("snapshot-board")!, true);
      renderBoard(graph, doc.cells || []);
    })
    .catch(() => {
      title.textContent = "快照无法打开";
      error.textContent = "链接无效，或快照尚未同步到这台服务器。";
      error.hidden = false;
    });
}
