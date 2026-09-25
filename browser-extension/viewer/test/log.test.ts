import assert from "node:assert/strict";
import test from "node:test";

import { parseLine, renderLog } from "../src/log.ts";
import { clearChrome, installChrome, page } from "./mock.ts";

/**
 * 日志视图。拆行是这里全部的判断力所在：时间戳两种写法、级别整词匹配、
 * 整行 JSON 走字段，其余都交给 CSS。之前一行没测。
 */

test("ISO 8601 时间戳被切出来，正文不含它", () => {
  const line = parseLine("2026-09-16T03:04:05.123Z INFO 启动完成");
  assert.equal(line.time, "2026-09-16T03:04:05.123Z");
  assert.equal(line.level, "info");
  assert.equal(line.message, "INFO 启动完成");
});

test("空格分隔的时间戳同样认，方括号包起来也认", () => {
  assert.equal(parseLine("2026-09-16 03:04:05 warn 慢查询").time, "2026-09-16 03:04:05");
  assert.equal(parseLine("[2026-09-16 03:04:05] warn 慢查询").time, "2026-09-16 03:04:05");
});

test("带时区偏移的时间戳也认", () => {
  assert.equal(parseLine("2026-09-16T03:04:05+08:00 error x").time, "2026-09-16T03:04:05+08:00");
});

test("没有时间戳时 time 是空串，整行都是正文", () => {
  const line = parseLine("error 连不上数据库");
  assert.equal(line.time, "");
  assert.equal(line.message, "error 连不上数据库");
});

test("WARNING 归到 warn，大小写不论", () => {
  assert.equal(parseLine("WARNING 磁盘快满了").level, "warn");
  assert.equal(parseLine("Error 崩了").level, "error");
});

test("级别是整词匹配，不会把 information 当成 info", () => {
  assert.equal(parseLine("informational message").level, null);
});

test("级别只在行首 40 个字符里找，后面的词不影响着色", () => {
  const far = `${"x".repeat(45)} error 这个 error 在正文里`;
  assert.equal(parseLine(far).level, null);
});

test("认不出级别的行 level 为 null（这种行永远显示）", () => {
  assert.equal(parseLine("一行没有级别的话").level, null);
});

test("整行 JSON 从字段里取时间和级别", () => {
  const raw = '{"time":"2026-09-16T03:04:05Z","level":"warn","msg":"慢"}';
  const line = parseLine(raw);
  assert.equal(line.time, "2026-09-16T03:04:05Z");
  assert.equal(line.level, "warn");
  assert.equal(line.message, raw, "消息保留原始整行");
  assert.deepEqual(line.json, { time: "2026-09-16T03:04:05Z", level: "warn", msg: "慢" });
});

test("JSON 的字段名认多种写法", () => {
  const line = parseLine('{"@timestamp":"2026-09-16T00:00:00Z","severity":"ERROR","m":"x"}');
  assert.equal(line.time, "2026-09-16T00:00:00Z");
  assert.equal(line.level, "error");
});

test("数字时间戳也取得出来", () => {
  assert.equal(parseLine('{"ts":1758000000,"lvl":"info"}').time, "1758000000");
});

test("以 { 开头但不是合法 JSON 的行按普通日志拆", () => {
  const line = parseLine("{ 这不是 JSON info 呢");
  assert.equal(line.json, undefined);
  assert.equal(line.level, "info");
});

test("空文件渲染出的视图没有任何行", async () => {
  installChrome({ runtime: { getURL: (p: string) => p } });
  const dom = page("");
  const host = await renderLog(dom.window.document, "");
  assert.equal(host.querySelectorAll(".lfv-log-row").length, 0);
  assert.equal(host.querySelectorAll(".lfv-log-filter").length, 0, "没有级别就不该有开关");
  clearChrome();
});

test("只为文件里真出现过的级别生成开关", async () => {
  installChrome({ runtime: { getURL: (p: string) => p } });
  const dom = page("");
  const host = await renderLog(dom.window.document, "info 一\nerror 二\ninfo 三\n没有级别的一行\n");
  const levels = Array.from(host.querySelectorAll(".lfv-log-filter input"), (el) =>
    (el as HTMLInputElement).dataset["level"]);
  assert.deepEqual(levels, ["error", "info"], "按 LEVELS 的严重度顺序，且只出现存在的级别");
  assert.equal(host.querySelectorAll(".lfv-log-row").length, 4);
  clearChrome();
});

test("关掉一个级别的开关＝给容器加隐藏类", async () => {
  installChrome({ runtime: { getURL: (p: string) => p } });
  const dom = page("");
  const host = await renderLog(dom.window.document, "info 一\nerror 二\n");
  const box = host.querySelector('input[data-level="info"]') as HTMLInputElement;
  box.checked = false;
  box.dispatchEvent(new dom.window.Event("change"));
  assert.equal((host.querySelector(".lfv-log") as HTMLElement).classList.contains("lfv-hide-info"), true);
  clearChrome();
});

test("末尾换行不产生一行空日志", async () => {
  installChrome({ runtime: { getURL: (p: string) => p } });
  const dom = page("");
  const host = await renderLog(dom.window.document, "info 一\n");
  assert.equal(host.querySelectorAll(".lfv-log-row").length, 1);
  clearChrome();
});
