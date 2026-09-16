/**
 * 扩展自己的展示页：强制拦截把一个本来会被下载的本地文件送到这里。
 *
 * 页面地址是 `viewer.html?file=<文件地址>`。取文件、塞进页面、交给 `prettify.ts` 里
 * 那条和普通文件页完全一样的渲染路径——所以这张页面上的表现和直接打开文件没有差别。
 */
import { show } from "./prettify.ts";

export async function load(doc: Document, url: string): Promise<void> {
  doc.title = decodeURIComponent(url.split("/").pop() ?? "");
  const response = await fetch(url);
  show(doc, url, await response.text());
}

/** 地址里的 `file` 参数就是要显示的文件。 */
export function fileOf(url: string): string {
  return new URL(url).searchParams.get("file") ?? "";
}
