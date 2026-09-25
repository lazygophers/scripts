import assert from "node:assert/strict";
import test from "node:test";

import { closeDiagramPreview, openDiagramPreview } from "../src/diagram-preview.ts";
import { page } from "./mock.ts";

/**
 * Mermaid 图的全屏预览。几何（适配、居中）是浏览器行为，jsdom 量不出尺寸，
 * 源码里也写明这种环境停在 100%——所以这里测的是状态机那一半：开关一次只开
 * 一个、缩放的上下限与步进、关闭的三条路、以及关闭后键盘监听真的被摘掉。
 */

function open(svg = "<svg viewBox='0 0 10 10'></svg>") {
  const dom = page("");
  const doc = dom.window.document;
  openDiagramPreview(doc, svg);
  const backdrop = doc.querySelector(".lfv-lightbox") as HTMLElement;
  return {
    dom, doc, backdrop,
    inner: backdrop.querySelector(".lfv-lightbox-inner") as HTMLElement,
    stage: backdrop.querySelector(".lfv-lightbox-stage") as HTMLElement,
    zoom: () => backdrop.querySelector(".lfv-lightbox-zoom")?.textContent,
    button: (label: string) =>
      Array.from(backdrop.querySelectorAll("button")).find((b) => b.textContent === label) as HTMLElement,
  };
}

/** 工具栏按钮点一下。 */
function click(dom: ReturnType<typeof page>, node: HTMLElement): void {
  node.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
}

test("打开后 SVG 进到画布里，mermaid 自带的尺寸限制被摘掉", () => {
  const { inner } = open("<svg width='100' height='50' viewBox='0 0 10 10'></svg>");
  const svg = inner.querySelector("svg") as SVGElement;
  assert.equal(svg.hasAttribute("width"), false);
  assert.equal(svg.hasAttribute("height"), false);
  assert.equal((svg as unknown as HTMLElement).style.maxWidth, "none");
});

test("工具栏四个按钮都在，缩放读数从 100% 起", () => {
  const { backdrop, zoom } = open();
  assert.deepEqual(
    Array.from(backdrop.querySelectorAll("button"), (b) => b.textContent),
    ["+", "−", "重置", "关闭"],
  );
  assert.equal(zoom(), "100%");
});

test("同一时刻只开一个：再开一次先把上一个关掉", () => {
  const { doc } = open();
  openDiagramPreview(doc, "<svg viewBox='0 0 10 10'></svg>");
  assert.equal(doc.querySelectorAll(".lfv-lightbox").length, 1);
});

test("放大缩小按 1.2 倍步进，读数跟着走", () => {
  const { dom, zoom, button } = open();
  click(dom, button("+"));
  assert.equal(zoom(), "120%");
  click(dom, button("−"));
  assert.equal(zoom(), "100%");
});

test("重置回到初始比例", () => {
  const { dom, zoom, button } = open();
  click(dom, button("+"));
  click(dom, button("+"));
  click(dom, button("重置"));
  assert.equal(zoom(), "100%");
});

test("放大有上限，一直点也停在 800%", () => {
  const { dom, zoom, button } = open();
  for (let i = 0; i < 30; i += 1) click(dom, button("+"));
  assert.equal(zoom(), "800%");
});

test("缩小有下限，一直点也停在 10%", () => {
  const { dom, zoom, button } = open();
  for (let i = 0; i < 30; i += 1) click(dom, button("−"));
  assert.equal(zoom(), "10%");
});

test("滚轮向上放大、向下缩小，且不让页面跟着滚", () => {
  const { dom, stage, zoom } = open();
  const up = new dom.window.WheelEvent("wheel", { deltaY: -100, cancelable: true, bubbles: true });
  stage.dispatchEvent(up);
  assert.equal(zoom(), "120%");
  assert.equal(up.defaultPrevented, true);

  stage.dispatchEvent(new dom.window.WheelEvent("wheel", { deltaY: 100, cancelable: true, bubbles: true }));
  assert.equal(zoom(), "100%");
});

test("双击放大一档", () => {
  const { dom, stage, zoom } = open();
  stage.dispatchEvent(new dom.window.MouseEvent("dblclick", { bubbles: true }));
  assert.equal(zoom(), "120%");
});

test("左键拖动改平移量，右键不拖", () => {
  const { dom, stage, inner } = open();
  const before = inner.style.transform;

  stage.dispatchEvent(new dom.window.MouseEvent("pointerdown", { button: 2, bubbles: true }) as unknown as Event);
  stage.dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 50, clientY: 40, bubbles: true }) as unknown as Event);
  assert.equal(inner.style.transform, before, "右键不该拖动");

  stage.dispatchEvent(new dom.window.MouseEvent("pointerdown", { button: 0, clientX: 0, clientY: 0, bubbles: true }) as unknown as Event);
  stage.dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 50, clientY: 40, bubbles: true }) as unknown as Event);
  assert.match(inner.style.transform, /translate\(50px, 40px\)/);

  stage.dispatchEvent(new dom.window.MouseEvent("pointerup", { bubbles: true }) as unknown as Event);
  stage.dispatchEvent(new dom.window.MouseEvent("pointermove", { clientX: 90, clientY: 90, bubbles: true }) as unknown as Event);
  assert.match(inner.style.transform, /translate\(50px, 40px\)/, "松手之后再移不该继续拖");
});

test("Esc 关掉预览", () => {
  const { dom, doc } = open();
  doc.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(doc.querySelector(".lfv-lightbox"), null);
});

test("点背景关掉，点图和工具栏不关", () => {
  const { dom, doc, backdrop, stage } = open();
  click(dom, stage);
  assert.ok(doc.querySelector(".lfv-lightbox"), "点画布不该关");
  click(dom, backdrop);
  assert.equal(doc.querySelector(".lfv-lightbox"), null);
});

test("关闭按钮关掉", () => {
  const { dom, doc, button } = open();
  click(dom, button("关闭"));
  assert.equal(doc.querySelector(".lfv-lightbox"), null);
});

test("关掉之后 Esc 不再被预览接管", async () => {
  const { dom, doc, button } = open();
  click(dom, button("关闭"));
  // 监听是靠 MutationObserver 观察到移除后摘掉的，等一个微任务
  await new Promise((resolve) => setTimeout(resolve, 0));
  const event = new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  doc.dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
});

test("没开着的时候关一下什么都不做", () => {
  const dom = page("");
  closeDiagramPreview(dom.window.document);
  assert.equal(dom.window.document.querySelector(".lfv-lightbox"), null);
});
