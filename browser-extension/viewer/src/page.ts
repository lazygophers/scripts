/**
 * 扩展自己的展示页：强制拦截把一个本来会被下载的本地文件送到这里。
 *
 * 取文件、塞进页面、交给 `prettify.ts` 里那条和普通文件页完全一样的渲染路径——所以
 * 这张页面上的表现和直接打开文件没有差别。地址里 `file` 参数的解析在
 * `intercept.ts`（拦截协议的收端），不在这里。
 */
import { show } from "./prettify.ts";

export async function load(doc: Document, url: string): Promise<void> {
  doc.title = decodeURIComponent(url.split("/").pop() ?? "");
  const response = await fetch(url);
  show(doc, url, await response.text());
}
