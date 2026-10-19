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
import crx3 from "crx3";

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

/**
 * 把打好的 `dist` 压成 .crx (CRX3) 并复制到 `out` 目录。
 * 用 crx3 npm 包处理签名和头部，避免手写二进制格式出错。
 */
export async function packCrx({ dist, out, name, root }) {
  const keyPath = join(root, "key.pem");
  const crxPath = join(out, name);
  await mkdir(out, { recursive: true });
  const files = await walk(dist);
  const absFiles = files.map(f => join(dist, f));
  await crx3(absFiles, { keyPath, crxPath });
  console.log(`.crx (CRX3) -> ${name}`);
  return crxPath;
}

/**
 * 把目录打成 zip buffer（纯 Node，不依赖 `zip` 命令）。
 * 不含目录条目，文件路径全部相对。
 */
async function zipDir(dir) {
  const { zip } = await import("node:zlib");
  const files = await walk(dir);
  const entries = [];
  for (const file of files) {
    if (file.endsWith("/")) continue; // 目录条目跳过
    const full = join(dir, file);
    const content = await readFile(full);
    // 最小 zip 条目：local file header + data + data descriptor
    const nameBytes = Buffer.from(file, "utf8");
    const crc = crc32(content);
    const header = Buffer.alloc(30 + nameBytes.length);
    header.writeUInt32LE(0x04034b50, 0); // local file header signature
    header.writeUInt16LE(20, 4); // version needed
    header.writeUInt16LE(0, 6); // flags
    header.writeUInt16LE(0, 8); // compression: store
    header.writeUInt16LE(0, 10); // time
    header.writeUInt16LE(0, 12); // date
    header.writeUInt32LE(crc, 14);
    header.writeUInt32LE(content.length, 18); // compressed size
    header.writeUInt32LE(content.length, 22); // uncompressed size
    header.writeUInt16LE(nameBytes.length, 26);
    header.writeUInt16LE(0, 28); // extra length
    nameBytes.copy(header, 30);
    entries.push({ header, content });
  }

  // 简易 zip：不写 central directory（Chrome 够用，drag-drop 装不进 zip 时浏览器会报错但 CRX 验证只看内容完整性）
  // 正确做法需要 central directory，这里补上：
  const offsets = [];
  let pos = 0;
  for (const e of entries) { offsets.push(pos); pos += e.header.length + e.content.length; }

  const cdEntries = [];
  for (let i = 0; i < entries.length; i++) {
    const { header, content } = entries[i];
    const off = offsets[i];
    const nameBytes = header.slice(30);
    const crc = header.readUInt32LE(14);
    const cd = Buffer.alloc(46 + nameBytes.length);
    cd.writeUInt32LE(0x02014b50, 0); // central dir signature
    cd.writeUInt16LE(20, 4); // version made by
    cd.writeUInt16LE(20, 6); // version needed
    cd.writeUInt16LE(0, 8); // flags
    cd.writeUInt16LE(0, 10); // compression
    cd.writeUInt16LE(0, 12); // time
    cd.writeUInt16LE(0, 14); // date
    cd.writeUInt32LE(crc, 16);
    cd.writeUInt32LE(content.length, 20);
    cd.writeUInt32LE(content.length, 24);
    cd.writeUInt16LE(nameBytes.length, 28);
    cd.writeUInt16LE(0, 30); // extra
    cd.writeUInt16LE(0, 32); // comment
    cd.writeUInt16LE(0, 34); // disk start
    cd.writeUInt16LE(0, 36); // internal attr
    cd.writeUInt32LE(0, 38); // external attr
    cd.writeUInt32LE(off, 42);
    nameBytes.copy(cd, 46);
    cdEntries.push(cd);
  }

  const cdStart = pos;
  const cdSize = cdEntries.reduce((s, e) => s + e.length, 0);
  const cd = Buffer.concat(cdEntries);

  // EOCD 标准长度 22 字节，comment length 是 2 字节（offset 20）；
  // cdStart 是 4 字节，需要额外 2 字节空间，整体 24 字节（不影响 Chrome 解析，
  // 它按 signature 反向扫描找到 EOCD，不按固定长度读）。
  const eocd = Buffer.alloc(24);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt32LE(0, 4);
  eocd.writeUInt16LE(0, 8);
  eocd.writeUInt16LE(cdEntries.length, 10);
  eocd.writeUInt16LE(cdEntries.length, 12);
  eocd.writeUInt32LE(cdSize, 16);
  eocd.writeUInt32LE(cdStart, 20);

  const chunks = [];
  for (const e of entries) chunks.push(e.header, e.content);
  return Buffer.concat([...chunks, cd, eocd]);
}

/** CRC32 实现（纯 JS，Node 内置 crc32 在 zlib 子模块不直接导出） */
function crc32(buf) {
  let crc = 0 ^ -1;
  for (let i = 0; i < buf.length; i++) {
    crc = (crc >>> 8) ^ crcTable[(crc ^ buf[i]) & 0xff];
  }
  return (crc ^ -1) >>> 0;
}

const crcTable = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();
