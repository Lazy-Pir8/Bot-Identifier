/**
 * extension/content.js — Reddit DOM Observer & Badge Injector
 * 
 * Dynamically finds author handles across Reddit (Old Reddit, New Reddit & Shreddit),
 * requests bot ratings from the local API server (http://127.0.0.1:8000),
 * and injects color-coded badges with detailed tooltips directly into the page.
 */

const API_SERVER = "http://127.0.0.1:8000";
const PENDING_QUEUE = new Map(); // username -> Array of DOM elements
let debounceTimer = null;

// Track scanned stats for the extension popup
let labeledCount = 0;

/**
 * Extract clean Reddit username from a link or element
 */
function extractUsername(element) {
  // 1. Shreddit custom element attribute
  if (element.hasAttribute("author")) {
    const a = element.getAttribute("author");
    if (a && a !== "[deleted]" && a !== "[removed]") return a.replace(/^u\//, "");
  }

  // 2. Check href pattern: /user/<username>/
  const href = element.getAttribute("href") || "";
  const match = href.match(/\/(?:user|u)\/([A-Za-z0-9_\-]+)/i);
  if (match) {
    const name = match[1];
    // Exclude system paths
    const systemNames = ["me", "settings", "null", "undefined", "notifications"];
    if (!systemNames.includes(name.toLowerCase())) {
      return name;
    }
  }

  // 3. Fallback to clean inner text if formatted as u/Name or author
  const text = (element.innerText || "").trim().replace(/^u\//, "");
  if (text && /^[A-Za-z0-9_\-]{3,20}$/.test(text) && !text.includes(" ")) {
    return text;
  }

  return null;
}

/**
 * Build badge DOM element with tooltip
 */
function createBadge(data) {
  const container = document.createElement("span");
  container.className = "rbi-badge-container";
  container.dataset.rbiUser = data.username;

  const scorePct = Math.round(data.score * 100);
  const labelClass = `rbi-label-${data.label.toLowerCase()}`;
  const isHighBot = data.label === "BOT" && data.score >= 0.85;

  // Badge pill
  const badge = document.createElement("span");
  badge.className = `rbi-badge ${labelClass} ${isHighBot ? "rbi-high-bot" : ""}`;
  badge.innerText = `${data.label} ${scorePct}%`;

  // Interactive tooltip
  const tooltip = document.createElement("div");
  tooltip.className = "rbi-tooltip";

  let notesHtml = "";
  if (data.notes && data.notes.length > 0) {
    notesHtml = `
      <div class="rbi-tooltip-notes">
        ${data.notes.map(n => `<div class="rbi-tooltip-note-item"><span>•</span> <span>${escapeHtml(n)}</span></div>`).join("")}
      </div>
    `;
  }

  const scores = data.scores || {};
  tooltip.innerHTML = `
    <div class="rbi-tooltip-header">
      <span class="rbi-tooltip-title">u/${escapeHtml(data.username)}</span>
      <span class="rbi-tooltip-score" style="color: ${data.color};">${data.label} (${scorePct}%)</span>
    </div>
    <div class="rbi-tooltip-breakdown">
      <div>Meta: ${(scores.meta || 0).toFixed(2)}</div>
      <div>Temporal: ${(scores.temporal || 0).toFixed(2)}</div>
      <div>Linguistic: ${(scores.linguistic || 0).toFixed(2)}</div>
      <div>Network: ${(scores.network || 0).toFixed(2)}</div>
    </div>
    ${notesHtml}
  `;

  container.appendChild(badge);
  container.appendChild(tooltip);
  return container;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.innerText = text || "";
  return div.innerHTML;
}

/**
 * Scan DOM for unparsed author elements
 */
function scanAuthors() {
  // Selectors covering Old Reddit, New Reddit, and Shreddit
  const selectors = [
    'a.author:not([data-rbi-checked])',                                  // Old Reddit
    'a[data-testid="comment_author"]:not([data-rbi-checked])',           // New Reddit
    'a[data-testid="post_author"]:not([data-rbi-checked])',              // New Reddit
    'a[slot="authorName"]:not([data-rbi-checked])',                      // Shreddit
    'shreddit-comment:not([data-rbi-checked])',                          // Shreddit Comment
    'a[href*="/user/"]:not([data-rbi-checked])',                         // Generic user links
    'a[href*="/u/"]:not([data-rbi-checked])'
  ];

  const elements = document.querySelectorAll(selectors.join(", "));
  let added = 0;

  elements.forEach((el) => {
    // Avoid re-processing
    el.dataset.rbiChecked = "true";

    // Skip nested duplicate targets (e.g. avatar images inside author anchors)
    if (el.querySelector("img") && el.children.length === 1 && !el.innerText.trim()) {
      return;
    }

    const username = extractUsername(el);
    if (!username) return;

    if (!PENDING_QUEUE.has(username)) {
      PENDING_QUEUE.set(username, []);
    }
    PENDING_QUEUE.get(username).push(el);
    added++;
  });

  if (added > 0) {
    scheduleBatchFetch();
  }
}

/**
 * Debounce batch API request
 */
function scheduleBatchFetch() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(processBatchQueue, 350);
}

/**
 * Send queued usernames to local backend and inject badges
 */
async function processBatchQueue() {
  if (PENDING_QUEUE.size === 0) return;

  const currentBatch = Array.from(PENDING_QUEUE.keys()).slice(0, 40);
  const elementsToUpdate = new Map();

  currentBatch.forEach((u) => {
    elementsToUpdate.set(u, PENDING_QUEUE.get(u));
    PENDING_QUEUE.delete(u);
  });

  try {
    const resp = await fetch(`${API_SERVER}/api/scan-users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ usernames: currentBatch }),
    });

    if (!resp.ok) return;

    const data = await resp.json();
    const results = data.results || {};

    elementsToUpdate.forEach((elements, username) => {
      const result = results[username];
      if (!result) return;

      elements.forEach((el) => {
        // Prevent duplicate badges
        if (el.nextSibling && el.nextSibling.classList && el.nextSibling.classList.contains("rbi-badge-container")) {
          return;
        }

        const badge = createBadge(result);
        
        // For shreddit-comment custom elements, attach badge inside author slot or header
        if (el.tagName.toLowerCase() === "shreddit-comment") {
          const authorSlot = el.querySelector('[slot="authorName"]') || el.shadowRoot?.querySelector('[slot="authorName"]');
          if (authorSlot) {
            authorSlot.parentNode.insertBefore(badge, authorSlot.nextSibling);
          }
        } else {
          el.parentNode.insertBefore(badge, el.nextSibling);
        }

        labeledCount++;
      });
    });

    // Notify extension popup if open
    try {
      chrome.runtime.sendMessage({ type: "UPDATE_STATS", count: labeledCount });
    } catch (_) {}

  } catch (err) {
    // API server is likely offline or unreachable
    console.debug("[Reddit Bot Identifier] Local API server at " + API_SERVER + " not responding.");
  }
}

// ─── Initialize Observers ───────────────────────────────────────────────────

scanAuthors();

// Watch for dynamically loaded comments (infinite scroll / expanding threads)
const observer = new MutationObserver(() => {
  scanAuthors();
});

observer.observe(document.body, {
  childList: true,
  subtree: true,
});

