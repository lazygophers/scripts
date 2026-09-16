import { CommandError, optionalString, requireString } from "../protocol.ts";
import { emitEvent } from "../events.ts";
import { requireApi } from "./context.ts";

/**
 * `chrome.webAuthenticationProxy`：把网页的 WebAuthn（无密码登录）请求转发出来，
 * CLI 用 `lg:wauth.complete` 回答案。attach 之后每次网页要钥匙/做断言，都会推
 * 一条 `lg:wauth.request` 事件；不回它网页会一直等到超时。
 */

interface WauthRequest {
  requestId: string;
  type: "get" | "create";
}

export async function wauthAttach(): Promise<{ attached: true }> {
  requireApi("webAuthenticationProxy", "proxying WebAuthn");
  await chrome.webAuthenticationProxy.attach();
  return { attached: true };
}

export async function wauthDetach(): Promise<{ detached: true }> {
  requireApi("webAuthenticationProxy", "proxying WebAuthn");
  await chrome.webAuthenticationProxy.detach();
  return { detached: true };
}

/**
 * 回答一条转发的请求。`kind` 决定走 completeGetRequest 还是 completeCreateRequest；
 * `httpStatusCode` / `headers` 是模拟出来的 WebAuthn 服务器响应。
 */
export async function wauthComplete(params: Record<string, unknown>): Promise<{ completed: string }> {
  requireApi("webAuthenticationProxy", "proxying WebAuthn");
  const requestId = requireString(params.request, "request", ", from the lg:wauth.request event");
  const kind = requireString(params.kind, "kind", ", get or create");
  if (kind !== "get" && kind !== "create") {
    throw new CommandError("invalid argument", "kind must be get or create");
  }
  const httpStatusCode = params.httpStatusCode;
  if (typeof httpStatusCode !== "number") {
    throw new CommandError("invalid argument", "httpStatusCode must be a number, e.g. 200");
  }
  const headers = params.headers;
  if (headers !== undefined && typeof headers !== "object") {
    throw new CommandError("invalid argument", "headers must be an object of name → value");
  }
  const response = {
    httpStatusCode,
    ...(headers === undefined ? {} : { headers: headers as Record<string, string> }),
  };
  if (kind === "get") {
    await chrome.webAuthenticationProxy.completeGetRequest(requestId, response);
  } else {
    await chrome.webAuthenticationProxy.completeCreateRequest(requestId, response);
  }
  return { completed: requestId };
}

/** WebAuthn 请求转发给订阅方（background 启动时接线）。 */
export function listenWauth(): void {
  const proxy = chrome.webAuthenticationProxy;
  proxy?.onRequest?.addListener((event: WauthRequest) => {
    emitEvent("lg:wauth.request", {
      request: event.requestId,
      kind: event.type,
      raw: event as unknown as Record<string, unknown>,
    });
  });
}
