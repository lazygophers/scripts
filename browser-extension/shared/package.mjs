/**
 * 打包：一条命令产出两个浏览器的安装包。
 *
 * Chrome 那个是 zip（拖进 `chrome://extensions` 就能装，商店上架要的也是 zip），
 * Firefox 那个是 `dist-firefox/` 目录本身加一个同样打好的 zip，用「临时载入附加组件」
 * 选里面的 `manifest.json` 就能跑。两边的源码和 manifest 都只有一份，差异在
 * `manifest.mjs` 里吸收。
 *
 * 打包前先自己验一遍：manifest 里点到名的文件必须真的在，少一个就让这条命令失败——
 * 缺文件的扩展装进浏览器才报错，那时离出错的改动已经隔了很远。
 */
import { spawnSync } from "node:child_process";
import { mkdir, readFile, readdir, rm } from "node:fs/promises";
import { join, relative } from "node:path";

/** sourcemap 只给开发看，装包里不带：viewer 带上是 27 MB，不带 2 MB 出头。 */
const SKIP = /\.map$/;

/**
 * 检查一份打好的 dist 是否装得起来。返回问题清单，空数组就是没问题。
 *
 * `files` 是 dist 里所有文件的相对路径，`manifest` 是已经解析好的 manifest 对象，
 * `pages` 是包里每张 html 的内容（文件名 -> 正文）。三个参数都是纯数据，所以这条规则
 * 本身单测得动，不必真打一次包。
 *
 * html 也要扫：页面用 `<script src>` 引的那个 js 名字写错了，manifest 一点问题都看不出来，
 * 装进浏览器也不报错，只是那张页面静静地什么都不做。
 */
export function checkPackage(files, manifest, pages = {}) {
  const problems = [];
  const has = (file) => files.includes(file);

  if (manifest.manifest_version !== 3) problems.push("manifest_version 不是 3");
  for (const field of ["name", "version"]) {
    if (!manifest[field]) problems.push(`manifest 少了 ${field}`);
  }

  for (const file of referenced(manifest)) {
    if (!has(file)) problems.push(`manifest 点到名的文件不在包里：${file}`);
  }
  for (const [name, html] of Object.entries(pages)) {
    for (const file of linked(html)) {
      if (!has(file)) problems.push(`${name} 里引的文件不在包里：${file}`);
    }
  }
  for (const file of files) {
    if (SKIP.test(file)) problems.push(`打包不该带上 sourcemap：${file}`);
  }
  return problems;
}

/** manifest 里点到名的所有文件。带 `*` 的通配条目跳过，那是给浏览器匹配用的。 */
function referenced(manifest) {
  const out = ["manifest.json"];
  for (const script of manifest.content_scripts ?? []) {
    out.push(...(script.js ?? []), ...(script.css ?? []));
  }
  const background = manifest.background ?? {};
  if (background.service_worker) out.push(background.service_worker);
  out.push(...(background.scripts ?? []));
  if (manifest.options_page) out.push(manifest.options_page);
  if (manifest.action?.default_popup) out.push(manifest.action.default_popup);
  for (const entry of manifest.web_accessible_resources ?? []) {
    out.push(...(entry.resources ?? []).filter((name) => !name.includes("*")));
  }
  return out;
}

/** 一张 html 里引到的本地文件。网址和页内锚点不算。 */
function linked(html) {
  const out = [];
  for (const [, file] of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
    if (/^(?:[a-z]+:|\/\/|#)/.test(file)) continue;
    out.push(file);
  }
  return out;
}

/** 一个目录里的所有文件，路径相对这个目录本身。 */
async function walk(dir, base = dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(full, base)));
    else out.push(relative(base, full));
  }
  return out;
}

/**
 * 验一份 dist 并压成 zip。验不过就抛错，调用方据此让整条命令失败。
 */
export async function packDist({ dist, out, name }) {
  const files = await walk(dist);
  const manifest = JSON.parse(await readFile(join(dist, "manifest.json"), "utf8"));
  const pages = {};
  for (const file of files.filter((name) => name.endsWith(".html"))) {
    pages[file] = await readFile(join(dist, file), "utf8");
  }
  const problems = checkPackage(files, manifest, pages);
  if (problems.length > 0) {
    throw new Error(`${name} 打包检查没过：\n  ${problems.join("\n  ")}`);
  }

  await mkdir(out, { recursive: true });
  const zip = join(out, name);
  await rm(zip, { force: true });
  // `-r` 递归，`-q` 安静，`-X` 不写 macOS 的扩展属性（装进浏览器没用，还会让包不可复现）。
  const result = spawnSync("zip", ["-rqX", zip, "."], { cwd: dist, stdio: "inherit" });
  if (result.error || result.status !== 0) {
    throw new Error(`zip 失败：${result.error?.message ?? `退出码 ${result.status}`}`);
  }
  return zip;
}
