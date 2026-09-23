/**
 * Mermaid 图的全屏预览：大图 + 缩放 + 拖动。
 *
 * 从文档里每张图的「查看大图」按钮进，Esc / 点背景 / 工具栏关闭出。
 * 缩放以鼠标位置为锚（滚轮、双击、工具栏按钮分别有各自的锚点），
 * 拖动用 Pointer Events + 指针捕获；同一时刻只开一个。
 */

const MIN_SCALE = 0.1;
const MAX_SCALE = 8;
/** 每次缩放的步进倍率。滚轮一格 / 按钮 / 双击共用。 */
const STEP = 1.2;

/** 打开一张图的全屏预览。svgHtml 是画好的 SVG 源码（mermaid 的 strict 档已净化）。 */
export function openDiagramPreview(doc: Document, svgHtml: string): void {
  closeDiagramPreview(doc);

  const backdrop = doc.createElement("div");
  backdrop.className = "lfv-lightbox";
  const stage = doc.createElement("div");
  stage.className = "lfv-lightbox-stage";
  const inner = doc.createElement("div");
  inner.className = "lfv-lightbox-inner";
  inner.innerHTML = svgHtml;
  const svg = inner.querySelector("svg");
  if (svg) {
    // mermaid 的产物自带 max-width 和 100% 宽高属性，在自由画布上会缩成一团；
    // 摘掉后由 viewBox 决定天然尺寸，缩放交给 transform。
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.style.maxWidth = "none";
  }

  const bar = doc.createElement("div");
  bar.className = "lfv-lightbox-bar";
  const readout = doc.createElement("span");
  readout.className = "lfv-lightbox-zoom";
  const zoomIn = barButton(doc, "+", "放大");
  const zoomOut = barButton(doc, "−", "缩小");
  const reset = barButton(doc, "重置", "重置");
  const close = barButton(doc, "关闭", "关闭");
  bar.append(readout, zoomIn, zoomOut, reset, close);

  stage.append(inner);
  backdrop.append(stage, bar);
  doc.body.append(backdrop);

  let scale = 1;
  let x = 0;
  let y = 0;

  const apply = () => {
    inner.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    readout.textContent = `${Math.round(scale * 100)}%`;
  };

  /** 以视口内 (px, py) 为锚缩放：锚点下的内容在缩放前后保持在同一位置。 */
  const zoomAt = (next: number, px: number, py: number) => {
    const clamped = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    if (clamped === scale) return;
    const cx = (px - x) / scale;
    const cy = (py - y) / scale;
    scale = clamped;
    x = px - cx * scale;
    y = py - cy * scale;
    apply();
  };

  const stageCenter = () => {
    const rect = stage.getBoundingClientRect();
    return [rect.left + rect.width / 2, rect.top + rect.height / 2] as const;
  };

  // 初始视图：整图适配进视口再居中。没有排版能力的环境（jsdom）量不出
  // 尺寸就停在 100%，坐标原点——预览的几何是浏览器行为，不归测试管。
  const native = inner.getBoundingClientRect();
  const fit = stage.clientWidth && native.width
    ? Math.min(stage.clientWidth / native.width, stage.clientHeight / native.height, 1)
    : 1;
  const centerAt = (s: number) => {
    const [cx, cy] = stageCenter();
    x = cx - (native.width * s) / 2;
    y = cy - (native.height * s) / 2;
  };
  scale = fit;
  centerAt(fit);
  apply();

  const onWheel = (event: WheelEvent) => {
    event.preventDefault();
    zoomAt(scale * (event.deltaY < 0 ? STEP : 1 / STEP), event.clientX, event.clientY);
  };

  let dragging = false;
  let startPx = 0;
  let startPy = 0;
  let startX = 0;
  let startY = 0;
  const onPointerDown = (event: PointerEvent) => {
    if (event.button !== 0) return;
    dragging = true;
    startPx = event.clientX;
    startPy = event.clientY;
    startX = x;
    startY = y;
    stage.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  };
  const onPointerMove = (event: PointerEvent) => {
    if (!dragging) return;
    x = startX + event.clientX - startPx;
    y = startY + event.clientY - startPy;
    apply();
  };
  const onPointerUp = (event: PointerEvent) => {
    dragging = false;
    stage.releasePointerCapture?.(event.pointerId);
  };
  const onDblClick = (event: MouseEvent) => {
    zoomAt(scale * STEP, event.clientX, event.clientY);
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDiagramPreview(doc);
    }
  };
  // 点背景关闭：目标是 backdrop 才关，点到图和工具栏都不算。
  const onBackdropClick = (event: MouseEvent) => {
    if (event.target === backdrop) closeDiagramPreview(doc);
  };

  stage.addEventListener("wheel", onWheel, { passive: false });
  stage.addEventListener("pointerdown", onPointerDown);
  stage.addEventListener("pointermove", onPointerMove);
  stage.addEventListener("pointerup", onPointerUp);
  stage.addEventListener("pointercancel", onPointerUp);
  stage.addEventListener("dblclick", onDblClick);
  backdrop.addEventListener("click", onBackdropClick);
  zoomIn.addEventListener("click", () => zoomAt(scale * STEP, ...stageCenter()));
  zoomOut.addEventListener("click", () => zoomAt(scale / STEP, ...stageCenter()));
  reset.addEventListener("click", () => {
    scale = fit;
    centerAt(fit);
    apply();
  });
  close.addEventListener("click", () => closeDiagramPreview(doc));
  doc.addEventListener("keydown", onKey, true);
  // 关闭时把自己挂的键盘监听摘掉：lightbox 移除了，监听不该还活着。
  // 只观察本 backdrop 的移除，别的节点变动一律不碰。
  const observer = new MutationObserver(() => {
    if (!backdrop.isConnected) {
      observer.disconnect();
      doc.removeEventListener("keydown", onKey, true);
    }
  });
  observer.observe(doc.body, { childList: true });
}

/** 关掉当前开着的预览（没有就什么都不做）。 */
export function closeDiagramPreview(doc: Document): void {
  doc.querySelector(".lfv-lightbox")?.remove();
}

function barButton(doc: Document, label: string, title: string): HTMLButtonElement {
  const button = doc.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.title = title;
  button.className = "lfv-lightbox-btn";
  return button;
}
