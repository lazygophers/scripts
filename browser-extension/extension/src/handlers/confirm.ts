import { decide, rememberApproval } from "../policy.ts";
import { CommandError } from "../protocol.ts";

/**
 * 高危动作的确认（spec 4.4）。**裁决就在这里**，不再问 daemon。
 *
 * 2026-09-14 的反转之前，是 daemon 读 `browse.yaml` 决定要不要问、然后发一条
 * `lg:confirm.request` 过来让插件弹框。现在策略存在 `chrome.storage.local`，这个函数
 * 自己查（`policy.decide`）、自己问、自己记住（per_domain 的免确认名单）。
 *
 * 入口只剩一个：handlers 在真正要动手的那一行调 `confirm()`。它知道确切的动作和目标
 * URL —— 这正是裁决和弹框都需要的东西，所以裁决放在这里是最省的位置。
 *
 * `background.ts` 装的 hook 是 UI（一个弹窗）。它会把答案记几秒，免得同一个问题因为
 * 重试之类的原因弹两次；那是 UI 去抖，不是策略。
 *
 * 默认 hook 一律允许，只对单测有意义：`background.ts` 总会把它换掉。
 */
export type RiskyAction =
  | "readCookies"
  | "writeCookies"
  | "readLocalStorage"
  | "writeLocalStorage"
  | "evalMainWorld"
  | "download"
  | "readHistory"
  | "writeHistory"
  | "readBookmarks"
  | "writeBookmarks";

export interface ConfirmRequest {
  action: RiskyAction;
  /** The wire method that triggered it, e.g. `storage.getCookies`. */
  method: string;
  /** Page URL the action targets, or null when it is browser-wide. */
  url: string | null;
}

export type ConfirmHook = (request: ConfirmRequest) => Promise<boolean>;

let hook: ConfirmHook = async () => true;

export function setConfirmHook(fn: ConfirmHook): void {
  hook = fn;
}

/**
 * 查策略 → 该问就问 → 不同意就抛。调用方不 catch。
 *
 * fail closed：hook 抛异常也当成拒绝，不当成允许。
 */
export async function confirm(request: ConfirmRequest): Promise<void> {
  if ((await decide(request.url)) === "allow") {
    return;
  }
  let approved = false;
  try {
    approved = await hook(request);
  } catch {
    approved = false;
  }
  if (!approved) {
    throw new CommandError(
      "lg:user rejected",
      `user denied ${request.action} for ${request.method} on ${request.url ?? "the browser"}`,
    );
  }
  // per_domain：同意过的域名下次不再问。其余模式什么都不记。
  await rememberApproval(request.url);
}
