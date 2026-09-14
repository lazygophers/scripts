/**
 * The toolbar panel, spec 4.5: list the domains that `confirm_mode:
 * per_domain` has stopped asking about, and let the user take any of them
 * back. The list lives in `browse.yaml` on the Python side; the panel goes
 * through the service worker, which relays to the daemon.
 */
const list = document.getElementById("list") as HTMLUListElement | null;
const statusNode = document.getElementById("status");

async function call(op: string, domain?: string): Promise<string[]> {
  const reply = await chrome.runtime.sendMessage({ type: "browse-approvals", op, domain });
  if (!reply?.ok) {
    throw new Error(reply?.error ?? "no answer from the service worker");
  }
  return reply.domains as string[];
}

function render(domains: string[]): void {
  if (!list) {
    return;
  }
  list.replaceChildren();
  if (statusNode) {
    statusNode.textContent = domains.length
      ? `${domains.length} domain${domains.length > 1 ? "s" : ""}`
      : "None yet — every risky action still asks.";
  }
  for (const domain of domains) {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.className = "domain";
    name.textContent = domain;
    const button = document.createElement("button");
    button.textContent = "Revoke";
    button.addEventListener("click", () => {
      button.disabled = true;
      void run(() => call("revoke", domain));
    });
    row.append(name, button);
    list.append(row);
  }
}

async function run(body: () => Promise<string[]>): Promise<void> {
  try {
    render(await body());
  } catch (err) {
    if (statusNode) {
      statusNode.textContent = err instanceof Error ? err.message : String(err);
    }
  }
}

void run(() => call("list"));
