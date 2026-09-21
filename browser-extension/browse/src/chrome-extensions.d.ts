/**
 * `@types/chrome` 0.3.0 仍缺的一批命名空间/成员（dns、processes、readingList、
 * webAuthenticationProxy 等，都是 2026-09-16 扩容用到的；2026-09-21 复核 0.3.0
 * 依旧缺，typecheck 全靠本文件）。就地把缺的形状补在这里；类型包追上后
 * 本文件应随冲突一起清掉。
 */
declare namespace chrome {
  namespace dns {
    interface ResolveResult {
      address: string;
      isCached: boolean;
    }
    function resolve(hostname: string): Promise<ResolveResult>;
  }

  namespace processes {
    interface Process {
      id: number;
      cpu: number;
      network: number;
      privateMemory: number;
      type: string;
      tasks: { tabId?: number; title?: string }[];
    }
    function processes(): Promise<Record<number, Process>>;
  }

  namespace readingList {
    interface ReadingListEntry {
      id: number;
      url: string;
      title: string;
      creationTime: number;
      lastUpdateTime: number;
      hasBeenRead: boolean;
    }
    interface QueryOptions {
      url?: string;
      title?: string;
    }
    function query(options?: QueryOptions): Promise<ReadingListEntry[]>;
    interface AddOptions {
      url: string;
      title: string;
      hasBeenRead?: boolean;
    }
    function add(options: AddOptions): Promise<ReadingListEntry>;
    interface UpdateOptions {
      id: number;
      url?: string;
      title?: string;
      hasBeenRead?: boolean;
    }
    function update(options: UpdateOptions): Promise<ReadingListEntry>;
    function remove(options: { id: number }): Promise<void>;
  }

  namespace webAuthenticationProxy {
    interface RequestEvent extends Record<string, unknown> {
      requestId: string;
      type: "get" | "create";
    }
    interface Response {
      httpStatusCode: number;
      headers?: Record<string, string>;
    }
    function attach(): Promise<void>;
    function detach(): Promise<void>;
    function completeGetRequest(requestId: string, response: Response): Promise<void>;
    function completeCreateRequest(requestId: string, response: Response): Promise<void>;
    const onRequest: events.Event<(event: RequestEvent) => void>;
  }

  namespace printing {
    function getJobs(): Promise<Job[]>;
  }

  namespace sidePanel {
    function close(options?: { tabId?: number }): Promise<void>;
  }

  namespace userScripts {
    function reset(): Promise<void>;
    interface UserScriptFilter {
      id?: string;
    }
  }

  namespace declarativeContent {
    class PageUrlMatcher {
      constructor(options: { urlPrefix?: string; urlMatches?: string });
    }
  }
}
