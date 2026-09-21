import { CommandError, optionalString, requireString } from "../protocol.ts";
import { emitEvent } from "../events.ts";
import { resolveContextOnce, requireApi } from "./context.ts";

/**
 * 扩展自己的三个交互面：commands（快捷键）、sidePanel（侧边栏）、omnibox
 * （地址栏关键词 `br`，manifest 里注册）。这三个是 Chrome 的 manifest key，
 * 不是 permission。
 */

export async function commandsList(): Promise<{ commands: chrome.commands.Command[] }> {
  requireApi("commands", "listing keyboard commands");
  return { commands: await chrome.commands.getAll() };
}

export async function sidePanelOpen(params: Record<string, unknown>): Promise<{ opened: true }> {
  requireApi("sidePanel", "opening the side panel");
  // 旧版 @types 的 OpenOptions 要求 tabId；运行时（Chrome 116+）可省
  const open = chrome.sidePanel.open as (options?: { tabId?: number }) => Promise<void>;
  if (params.context !== undefined || params.matchUrl !== undefined) {
    const target = await resolveContextOnce(params);
    await open({ tabId: target.tabId });
  } else {
    await open({});
  }
  return { opened: true };
}

export async function sidePanelClose(params: Record<string, unknown>): Promise<{ closed: true }> {
  requireApi("sidePanel.close", "closing the side panel");
  if (params.context !== undefined || params.matchUrl !== undefined) {
    const target = await resolveContextOnce(params);
    await chrome.sidePanel.close({ tabId: target.tabId });
  } else {
    await chrome.sidePanel.close({});
  }
  return { closed: true };
}

/** `openPanelOnActionClick`：点工具栏图标时开侧边栏（而不是弹 popup）。 */
export async function sidePanelBehavior(
  params: Record<string, unknown>,
): Promise<{ openPanelOnActionClick: boolean }> {
  requireApi("sidePanel.setPanelBehavior", "changing the side panel behavior");
  const openPanelOnActionClick = params.openPanelOnActionClick === true;
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick });
  return { openPanelOnActionClick };
}

export async function omniboxSetDefault(
  params: Record<string, unknown>,
): Promise<{ default: string }> {
  requireApi("omnibox.setDefaultSuggestion", "setting the omnibox default suggestion");
  const description = requireString(
    params.description,
    "description",
    ', omnibox suggestion format, e.g. "搜: %s"',
  );
  await chrome.omnibox.setDefaultSuggestion({ description });
  return { default: description };
}

/** 快捷键和地址栏输入转发给订阅方（background 启动时接线）。 */
export function listenUi(): void {
  chrome.commands?.onCommand?.addListener((command) => {
    emitEvent("lg:commands.triggered", { command });
  });
  chrome.omnibox?.onInputStarted?.addListener(() => {
    emitEvent("lg:omnibox.input", { phase: "started" });
  });
  chrome.omnibox?.onInputChanged?.addListener((text) => {
    emitEvent("lg:omnibox.input", { phase: "changed", text });
  });
  chrome.omnibox?.onInputEntered?.addListener((text, disposition) => {
    emitEvent("lg:omnibox.input", { phase: "entered", text, disposition });
  });
  chrome.omnibox?.onInputCancelled?.addListener(() => {
    emitEvent("lg:omnibox.input", { phase: "cancelled" });
  });
}
