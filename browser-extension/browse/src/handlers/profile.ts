import { CommandError, optionalString } from "../protocol.ts";
import { profileName, saveProfileName } from "../native-port.ts";

/**
 * profile 标识：同一浏览器多 profile 时区分连接（每个 profile 的
 * `chrome.storage` 独立，各自设各自的名字）。hello 和心跳都会带上它，
 * bridge 侧由心跳刷新，所以 set 完不用重连。
 */

const MAX_LEN = 64;

/** `lg:profile.get`：报自己当前的 profile 名。 */
export async function profileGet(): Promise<{ profile: string }> {
  return { profile: await profileName() };
}

/** `lg:profile.set`：设/清自己的 profile 名（空串 = 清除）。 */
export async function profileSet(params: Record<string, unknown>): Promise<{ profile: string }> {
  const name = optionalString(params.name, "name");
  if (name === undefined) {
    throw new CommandError("invalid argument", "name is required for lg:profile.set");
  }
  if (name.length > MAX_LEN) {
    throw new CommandError("invalid argument", `name must be at most ${MAX_LEN} characters`);
  }
  await saveProfileName(name);
  return { profile: name };
}
