// Fetches /deployments/{domain}/logs/status after the page has already
// navigated in, so the SSM round-trip shows a loading state instead of
// blocking navigation from /deployments.
import type { ContainerTailResult, RunnerStatusResult } from "./types.js";
import { debugLog } from "./debug.js";

const TAIL_POLL_INTERVAL_MS = 5000;
const tailTimers = new Map<string, number>();

function domainFromPath(): string | null {
  const match = window.location.pathname.match(/^\/deployments\/([^/]+)\/logs$/);
  return match ? match[1] : null;
}

async function fetchTail(domain: string, container: string, pre: HTMLElement): Promise<void> {
  const response = await fetch(`/deployments/${domain}/logs/containers/${container}/tail`);
  const result = (await response.json()) as ContainerTailResult;
  debugLog(`container tail: ${container}`, result);
  // result.lines is pre-rendered HTML (ANSI colour codes turned into <span>s server-side).
  if (result.lines) pre.innerHTML = result.lines.join("\n");
}

function copyToClipboard(button: HTMLButtonElement, text: string): void {
  void navigator.clipboard.writeText(text).then(() => {
    const original = button.textContent;
    button.textContent = "Copied!";
    window.setTimeout(() => {
      button.textContent = original;
    }, 1500);
  });
}

function renderAdminCredentials(email: string, password: string): void {
  const card = document.getElementById("admin-credentials-card");
  const emailEl = document.getElementById("admin-credentials-email");
  const passwordEl = document.getElementById("admin-credentials-password");
  const toggleButton = document.getElementById("admin-credentials-toggle-password");
  const copyEmailButton = document.getElementById("admin-credentials-copy-email");
  const copyPasswordButton = document.getElementById("admin-credentials-copy-password");
  if (!card || !emailEl || !passwordEl) return;

  emailEl.textContent = email;
  let revealed = false;

  if (toggleButton instanceof HTMLButtonElement) {
    toggleButton.addEventListener("click", () => {
      revealed = !revealed;
      passwordEl.textContent = revealed ? password : "••••••••";
      toggleButton.textContent = revealed ? "Hide" : "Show";
    });
  }
  if (copyEmailButton instanceof HTMLButtonElement) {
    copyEmailButton.addEventListener("click", () => copyToClipboard(copyEmailButton, email));
  }
  if (copyPasswordButton instanceof HTMLButtonElement) {
    copyPasswordButton.addEventListener("click", () => copyToClipboard(copyPasswordButton, password));
  }

  card.hidden = false;
}

function stopTail(container: string, button: HTMLButtonElement): void {
  const timer = tailTimers.get(container);
  if (timer !== undefined) window.clearInterval(timer);
  tailTimers.delete(container);
  button.textContent = "Tail";
}

function startTail(domain: string, container: string, button: HTMLButtonElement, pre: HTMLElement): void {
  button.textContent = "Stop tailing";
  void fetchTail(domain, container, pre);
  tailTimers.set(
    container,
    window.setInterval(() => void fetchTail(domain, container, pre), TAIL_POLL_INTERVAL_MS)
  );
}

function renderContainers(domain: string, containers: Record<string, string[]>): void {
  const section = document.getElementById("containers-section");
  if (!section) return;
  for (const name of Object.keys(containers).sort()) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const container_name = /^(glow-)(.+)(-\d+)$/.exec(name);
    summary.textContent = container_name ? container_name[2] : name.replace(/[-_]/g, " ");
    summary.classList.add('capitalize');

    const tailButton = document.createElement("button");
    tailButton.type = "button";
    tailButton.textContent = "Tail";
    tailButton.className = "button button-ghost";
    summary.append(" ", tailButton);

    const pre = document.createElement("pre");
    // containers[name] lines are pre-rendered HTML (ANSI colour codes turned into <span>s server-side).
    pre.innerHTML = containers[name].join("\n");

    tailButton.addEventListener("click", (event) => {
      // Button lives inside <summary> — don't let the click also toggle <details>.
      event.preventDefault();
      if (tailTimers.has(name)) {
        stopTail(name, tailButton);
      } else {
        startTail(domain, name, tailButton, pre);
      }
    });

    details.append(summary, pre);
    section.append(details);
  }
}

async function load(domain: string): Promise<void> {
  const statusLoadingEl = document.getElementById("status-loading");
  const statusErrorEl = document.getElementById("status-error");
  const statusCardEl = document.getElementById("status-card");
  const containersLoadingEl = document.getElementById("containers-loading");
  const containersErrorEl = document.getElementById("containers-error");

  const response = await fetch(`/deployments/${domain}/logs/status`);
  const result = (await response.json()) as RunnerStatusResult;
  debugLog("runner status", result);

  if (statusLoadingEl) statusLoadingEl.hidden = true;
  if (containersLoadingEl) containersLoadingEl.hidden = true;

  if (result.error) {
    if (statusErrorEl) {
      statusErrorEl.textContent = result.error;
      statusErrorEl.hidden = false;
    }
  } else if (result.status && statusCardEl) {
    const healthEl = document.getElementById("status-health");
    const gitRefEl = document.getElementById("status-git-ref");
    const gitCommitEl = document.getElementById("status-git-commit");
    if (healthEl) healthEl.textContent = result.status.health;
    if (gitRefEl) gitRefEl.textContent = result.status.git_ref;
    if (gitCommitEl) gitCommitEl.textContent = result.status.git_commit;
    statusCardEl.hidden = false;
  }

  const adminCredentialsErrorEl = document.getElementById("admin-credentials-error");
  if (result.admin_credentials_error) {
    if (adminCredentialsErrorEl) {
      adminCredentialsErrorEl.textContent = result.admin_credentials_error;
      adminCredentialsErrorEl.hidden = false;
    }
  } else if (result.admin_credentials) {
    renderAdminCredentials(result.admin_credentials.email, result.admin_credentials.password);
  }

  if (result.containers_error) {
    if (containersErrorEl) {
      containersErrorEl.textContent = result.containers_error;
      containersErrorEl.hidden = false;
    }
  } else if (result.containers) {
    renderContainers(domain, result.containers);
  }
}

export function init(): void {
  const domain = domainFromPath();
  if (domain) void load(domain);
}

if (!(globalThis as { __TEST__?: boolean }).__TEST__) init();
