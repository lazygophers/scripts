/**
 * 审计日志：存在 `chrome.storage.local` 里（spec 4.6）。
 *
 * 2026-09-14 从 Python 侧的 JSONL 文件搬过来。用户知道并接受这条路的两个代价：
 * **配额 10 MB 封顶**、**扩展一卸载日志就没了**。换来的是配置和日志只有一处。
 *
 * 两条不能丢的性质：
 *
 * - **只记「对哪个域名做了什么」**：不记页面正文、不记 cookie 值、不记表单输入。进来的
 *   每一条都过 `clean()`（先脱敏再截断），顺序绑死在那个函数里。
 * - **记账失败绝不能把指令搞挂**：`record()` 吞掉所有异常。审计是旁路，写不进去是审计
 *   的问题，不是这条指令的问题。
 *
 * ## 配额
 *
 * 审计是只增不减的流水，必然撞 10 MB 上限。撞上时 `storage.set` 会抛
 * `QUOTA_BYTES quota exceeded`。对策是环形淘汰：丢掉最老的一批再重试，最多重试
 * `EVICT_ROUNDS` 轮；还是写不进去就放弃这一条（并且**不抛**）。
 *
 * ponytail: 整份日志存在一个 key 下，每记一条就重写全部。2000 条 × 几百字节 ≈ 几百 KB
 * 的重写，对「一秒几条指令」的量级够用。真嫌慢了再改成按天分 key。
 */
import { clean } from "./redact.ts";
import { getConfig } from "./policy.ts";

export const AUDIT_KEY = "browse:audit";

/** 条数硬上限。先于配额生效，省得每次都要撞一次墙才淘汰。 */
export const MAX_ENTRIES = 2000;

/** 撞配额后每轮丢掉多少比例的最老记录。 */
const EVICT_RATIO = 0.25;

/** 撞配额后最多重试几轮。丢到这个程度还写不进去，就不是审计能解决的问题了。 */
const EVICT_ROUNDS = 4;

export interface AuditEntry {
  /** ISO 时间戳。 */
  ts: string;
  method: string;
  /** 目标域名，浏览器全局的动作是 null。 */
  domain: string | null;
  /** 高危动作名，非高危是 null。 */
  action: string | null;
  result: "success" | "error" | "denied";
  ms: number | null;
  error?: string;
}

async function load(): Promise<AuditEntry[]> {
  try {
    const got = await chrome.storage.local.get(AUDIT_KEY);
    const list = got?.[AUDIT_KEY];
    return Array.isArray(list) ? (list as AuditEntry[]) : [];
  } catch {
    return [];
  }
}

/** 超期的记录（`audit_retention_days`），`<= 0` 表示永不删除。 */
function dropExpired(entries: AuditEntry[], days: number): AuditEntry[] {
  if (days <= 0) {
    return entries;
  }
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return entries.filter((entry) => {
    const at = Date.parse(entry.ts);
    return Number.isNaN(at) || at >= cutoff;
  });
}

/**
 * 写一批记录，撞配额就丢最老的重试。写成了返回 true。
 *
 * 导出只为测试能直接驱动淘汰这条路 —— 真造 10 MB 数据太慢，测试用一个会抛配额错的
 * 假 storage 更快也更准。
 */
export async function writeWithEviction(entries: AuditEntry[]): Promise<boolean> {
  let list = entries.slice(-MAX_ENTRIES);
  for (let round = 0; round <= EVICT_ROUNDS; round += 1) {
    try {
      await chrome.storage.local.set({ [AUDIT_KEY]: list });
      return true;
    } catch (err) {
      if (list.length <= 1) {
        console.warn("[browse] 审计写不进去，放弃这一条", err);
        return false;
      }
      const drop = Math.max(1, Math.floor(list.length * EVICT_RATIO));
      list = list.slice(drop); // 最老的在前面
      console.warn(`[browse] 审计撞配额，丢掉最老的 ${drop} 条后重试`);
    }
  }
  return false;
}

/**
 * 记一行。**永不抛**：审计是旁路，写不进去不该让这条指令失败。
 *
 * `params` 一个字都不会原样进来 —— 只从里面取域名。
 */
export async function record(entry: AuditEntry): Promise<void> {
  try {
    const config = await getConfig();
    if (!config.audit) {
      return;
    }
    const kept = dropExpired(await load(), config.audit_retention_days);
    // clean 先脱敏再截断，顺序绑死在它里面
    kept.push(clean(entry) as AuditEntry);
    await writeWithEviction(kept);
  } catch (err) {
    console.warn("[browse] 审计记不下来，指令照常", err);
  }
}

/** 读回审计，最新的在最后。`limit` 只取最后 N 条。 */
export async function read(limit?: number): Promise<AuditEntry[]> {
  const entries = await load();
  return limit && limit > 0 ? entries.slice(-limit) : entries;
}

export async function clear(): Promise<number> {
  const count = (await load()).length;
  await chrome.storage.local.remove(AUDIT_KEY);
  return count;
}
