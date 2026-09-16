/**
 * 强制拦截的重定向规则。
 *
 * 有几类文件浏览器判定为「不能内联显示」，直接存盘，页面根本不存在，内容脚本也就无从注入。
 * 实测（Chrome + macOS）落在这一类的只有 `.yaml`/`.yml`（`application/x-yaml`，不在 Blink
 * 可渲染名单）和 `.csv`（在 `kUnsupportedTextTypes` 拒绝名单）；`.md` 会内联渲染，
 * `.go`/`.log`/`.toml` 这些系统 MIME 表里没条目的走嗅探变成 `text/plain`，同样内联。
 * 出处：`chromium/chromium:net/base/mime_util.cc`、
 * `chromium/chromium:third_party/blink/common/mime_util/mime_util.cc`，决策见
 * `docs/adr/0003-viewer-download-types-opt-in.md`。
 *
 * 拦截用 `declarativeNetRequest`：它在导航发生之前就把地址改掉，用户落在的就是渲染页，
 * 当前标签页、前进后退都是正常的一次导航。MV3 的普通扩展没有阻塞式 `webRequest`，
 * 这是唯一能做到「导航前换掉」的接口。
 */

/** 会被浏览器直接下载、因此需要拦的扩展名。 */
export const FORCED_EXTS = ["yaml", "yml", "csv"];

export type Rule = chrome.declarativeNetRequest.Rule;

/**
 * `@types/chrome` 把 `type` 和 `resourceTypes` 声明成枚举，而那两个枚举在运行时是
 * 浏览器给的对象，扩展代码里引不到；规则本身就是一份 JSON，照字面写完整体断言即可。
 */

/**
 * 规则表。`base` 是扩展自己的地址前缀（`chrome.runtime.getURL("")`）。
 *
 * 用动态规则而不是 manifest 里的静态规则集：跳转地址里要带上原文件的地址
 * （`\0` 是整段匹配），而扩展的地址前缀含扩展 id，写死在文件里就得连带把 id 也钉死。
 */
export function rules(base: string): Rule[] {
  return FORCED_EXTS.map((ext, index) => ({
    id: index + 1,
    priority: 1,
    action: {
      type: "redirect",
      redirect: { regexSubstitution: `${base}viewer.html?file=\\0` },
    },
    condition: {
      regexFilter: `^file:///.*\\.${ext}$`,
      // 只拦地址栏里的整页导航。页面里 fetch 同一个文件不受影响。
      resourceTypes: ["main_frame"],
    },
  }) as Rule);
}

/**
 * 开关拨到哪一档就让规则跟到哪一档。
 *
 * 关的时候把同一批 id 删掉即可——dNR 的动态规则存在浏览器里，重启还在，所以删干净了
 * 才是真的恢复原来的下载行为。
 */
export async function applyRules(on: boolean): Promise<void> {
  const list = rules(chrome.runtime.getURL(""));
  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: list.map((rule) => rule.id),
    addRules: on ? list : [],
  });
}
