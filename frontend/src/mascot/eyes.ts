/** 看板娘眼睛追踪（Research_KanbanMusume.md §2）。
 * 纯表现层：监听 mousemove，atan2 算角度 + 半径限制，更新瞳孔 transform。
 * 机器人造型下眼睛落在面罩上：cx=37/63、cy=47，瞳孔取 antd 蓝。
 * 与 CursorEventAdapter 共享鼠标事件源，但此处不进 LangGraph、不进任何存储。 */
const RADIUS = 4.5;
const EYE_CY = 47;

export function trackEyes(svg: SVGSVGElement): void {
  const left = makeEye(svg, 37, EYE_CY);
  const right = makeEye(svg, 63, EYE_CY);

  window.addEventListener("mousemove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const ang = Math.atan2(ev.clientY - cy, ev.clientX - cx);
    const px = Math.cos(ang) * RADIUS;
    const py = Math.sin(ang) * RADIUS;
    left.setAttribute("transform", `translate(${px} ${py})`);
    right.setAttribute("transform", `translate(${px} ${py})`);
  });
}

function makeEye(svg: SVGSVGElement, cx: number, cy: number): SVGGElement {
  const ns = "http://www.w3.org/2000/svg";
  const g = document.createElementNS(ns, "g");
  const white = document.createElementNS(ns, "circle");
  white.setAttribute("cx", String(cx)); white.setAttribute("cy", String(cy));
  white.setAttribute("r", "7"); white.setAttribute("fill", "#ffffff");
  const pupil = document.createElementNS(ns, "circle");
  pupil.setAttribute("cx", String(cx)); pupil.setAttribute("cy", String(cy));
  pupil.setAttribute("r", "3.5");
  pupil.setAttribute("fill", "var(--ant-primary, #1677ff)");
  g.appendChild(white); g.appendChild(pupil);
  svg.appendChild(g);
  return g;
}
