/**
 * extension/content.js — Reddit DOM Observer & Badge Injector
 * 
 * Dynamically finds author handles across Reddit (Old Reddit, New Reddit & Shreddit),
 * requests bot ratings from the local API server (http://127.0.0.1:8000),
 * and injects a single color-coded badge strictly to the right of the author's name.
 */

const API_SERVER = "http://127.0.0.1:8000";
const PENDING_QUEUE = new Map(); // username -> Array of DOM elements
let debounceTimer = null;

// Track scanned stats for the extension popup
let labeledCount = 0;

// Singleton global tooltip element attached to document.body (never clipped by parent overflow)
let globalTooltip = null;

function getOrCreateGlobalTooltip() {
  if (!globalTooltip || !globalTooltip.isConnected) {
    globalTooltip = document.createElement("div");
    globalTooltip.id = "rbi-global-tooltip";
    globalTooltip.className = "rbi-tooltip";
    document.body.appendChild(globalTooltip);
  }
  return globalTooltip;
}

function showTooltip(badgeEl, data) {
  const tooltip = getOrCreateGlobalTooltip();
  const scorePct = Math.round((data.score || 0) * 100);
  const scores = data.scores || {};

  let notesHtml = "";
  if (data.notes && data.notes.length > 0) {
    notesHtml = `
      <div class="rbi-tooltip-notes">
        ${data.notes.map(n => `<div class="rbi-tooltip-note-item"><span>•</span> <span>${escapeHtml(n)}</span></div>`).join("")}
      </div>
    `;
  }

  tooltip.innerHTML = `
    <div class="rbi-tooltip-header">
      <span class="rbi-tooltip-title">u/${escapeHtml(data.username)}</span>
      <span class="rbi-tooltip-score" style="color: ${data.color || '#fff'};">${scorePct}% Bot Likelihood</span>
    </div>
    <div class="rbi-tooltip-breakdown">
      <div>Meta: ${(scores.meta || 0).toFixed(2)}</div>
      <div>Temporal: ${(scores.temporal || 0).toFixed(2)}</div>
      <div>Linguistic: ${(scores.linguistic || 0).toFixed(2)}</div>
      <div>Network: ${(scores.network || 0).toFixed(2)}</div>
    </div>
    ${notesHtml}
  `;

  tooltip.style.display = "block";
  tooltip.style.visibility = "hidden"; // measure dimensions first

  const rect = badgeEl.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();

  // Position centered horizontally above badge
  let left = rect.left + window.scrollX + (rect.width / 2) - (tooltipRect.width / 2);
  let top = rect.top + window.scrollY - tooltipRect.height - 8;

  // Viewport bounds checking
  const padding = 10;
  if (left < padding) left = padding;
  if (left + tooltipRect.width > window.innerWidth - padding) {
    left = window.innerWidth - tooltipRect.width - padding;
  }

  // If clipped above top of viewport, flip to below badge
  if (rect.top - tooltipRect.height - 8 < 0) {
    top = rect.bottom + window.scrollY + 8;
  }

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
  tooltip.style.visibility = "visible";
  tooltip.style.opacity = "1";
}

function hideTooltip() {
  if (globalTooltip) {
    globalTooltip.style.display = "none";
    globalTooltip.style.visibility = "hidden";
  }
}

window.addEventListener("scroll", hideTooltip, { passive: true });

/**
 * Extract clean Reddit username from a link or element
 */
function extractUsername(element) {
  // 1. Shreddit custom element attribute or slot
  if (element.hasAttribute("author")) {
    const a = element.getAttribute("author");
    if (a && a !== "[deleted]" && a !== "[removed]") return a.replace(/^u\//, "");
  }

  // 2. Check href pattern: /user/<username>/ or /u/<username>/
  const href = element.getAttribute("href") || "";
  const match = href.match(/\/(?:user|u)\/([A-Za-z0-9_\-]+)/i);
  if (match) {
    const name = match[1];
    const systemNames = ["me", "settings", "null", "undefined", "notifications", "submit"];
    if (!systemNames.includes(name.toLowerCase())) {
      return name;
    }
  }

  // 3. Fallback to clean inner text if formatted as u/Name or Name
  const text = (element.innerText || "").trim().replace(/^u\//, "");
  if (text && /^[A-Za-z0-9_\-]{3,20}$/.test(text) && !text.includes(" ")) {
    return text;
  }

  return null;
}

/**
 * Validate that an element is strictly the author's name display element
 * (and NOT an avatar, icon, or mention inside comment body).
 */
function isValidAuthorElement(el, username) {
  // 1. Must NOT be an avatar, icon, or inside an avatar/icon container
  if (
    el.closest('[slot*="Avatar"], [slot*="avatar"], [data-testid*="avatar" i], [data-testid*="icon" i], [aria-label*="avatar" i], .avatar') ||
    el.matches('[slot*="Avatar"], [slot*="avatar"], [data-testid*="avatar" i], [aria-label*="avatar" i]')
  ) {
    return false;
  }

  // 2. Reject if element contains image/svg and has no visible text
  if (el.querySelector('faceplate-img, img, svg, faceplate-partial') && !el.innerText.trim()) {
    return false;
  }

  // 3. Must NOT be inside comment text body or markdown paragraphs (which are user mentions like u/someone, not authors)
  if (el.closest('[data-testid="comment-body"], .usertext-body, [slot="comment-body"], .md p, p')) {
    return false;
  }

  // 4. Must NOT be a link to generic user profile actions like settings or message
  const href = el.getAttribute("href") || "";
  if (href.includes("/message/") || href.includes("/settings/")) {
    return false;
  }

  // 5. Must contain text matching the username
  const text = (el.innerText || "").trim().replace(/^u\//i, "");
  if (!text) {
    return false;
  }
  const cleanWord = text.split(/[\s\n]+/)[0].replace(/^u\//i, "");
  if (cleanWord.toLowerCase() !== username.toLowerCase()) {
    return false;
  }

  // 6. Check if parent comment/post or immediate context ALREADY has a badge or was labeled
  const parentContainer = el.closest('shreddit-comment, shreddit-post, div.comment, [data-testid="comment"], [data-testid="post-container"], article, tr.thing');
  if (parentContainer && (parentContainer.dataset.rbiLabeled === "true" || parentContainer.querySelector('.rbi-badge'))) {
    return false;
  }

  // 7. Check sibling
  if (el.nextElementSibling && el.nextElementSibling.classList.contains("rbi-badge")) {
    return false;
  }

  return true;
}

/**
 * Return tier CSS class based on continuous 7-tier score spectrum
 */
function getTierClass(score) {
  if (score >= 0.86) return "rbi-tier-7";
  if (score >= 0.75) return "rbi-tier-6";
  if (score >= 0.60) return "rbi-tier-5";
  if (score >= 0.45) return "rbi-tier-4";
  if (score >= 0.30) return "rbi-tier-3";
  if (score >= 0.15) return "rbi-tier-2";
  return "rbi-tier-1";
}

/**
 * Build badge DOM element
 */
function createBadge(data) {
  const scorePct = Math.round((data.score || 0) * 100);
  const tierClass = getTierClass(data.score || 0);
  const isHighBot = (data.score || 0) >= 0.86;

  // Badge pill: Show ONLY percentage
  const badge = document.createElement("span");
  badge.className = `rbi-badge ${tierClass} ${isHighBot ? "rbi-high-bot" : ""}`;
  badge.innerText = `${scorePct}%`;
  badge.setAttribute("role", "status");
  badge.setAttribute("aria-label", `Bot likelihood: ${scorePct}%`);

  // Attach hover events to show/hide global body-level tooltip
  badge.addEventListener("mouseenter", () => showTooltip(badge, data));
  badge.addEventListener("mouseleave", hideTooltip);

  return badge;
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
  // Strictly target author name elements across Old Reddit, New Reddit, and Shreddit
  const selectors = [
    '[slot="authorName"]:not([data-rbi-checked])',                      // Shreddit comments & posts
    'a[data-testid="comment_author"]:not([data-rbi-checked])',           // New Reddit comments
    'a[data-testid="post_author"]:not([data-rbi-checked])',              // New Reddit posts
    'a[data-testid="author-link"]:not([data-rbi-checked])',              // Reddit web author links
    'a.author:not([data-rbi-checked])',                                  // Old Reddit
    'a[href*="/user/"]:not([data-rbi-checked])',                         // Generic user links
    'a[href*="/u/"]:not([data-rbi-checked])'
  ];

  const elements = document.querySelectorAll(selectors.join(", "));
  let added = 0;

  elements.forEach((el) => {
    // Avoid re-processing
    el.dataset.rbiChecked = "true";

    const username = extractUsername(el);
    if (!username) return;

    // Strict validation: must be author name text, NOT an avatar or comment body mention
    if (!isValidAuthorElement(el, username)) return;

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
        if (!el.isConnected) return;

        // Ensure this comment or post container hasn't already received a badge
        const parentContainer = el.closest('shreddit-comment, shreddit-post, div.comment, [data-testid="comment"], [data-testid="post-container"], article, tr.thing');
        if (parentContainer && (parentContainer.dataset.rbiLabeled === "true" || parentContainer.querySelector('.rbi-badge'))) {
          return;
        }

        // Prevent duplicate badge directly next to element
        if (el.nextElementSibling && el.nextElementSibling.classList.contains("rbi-badge")) {
          return;
        }

        const badge = createBadge(result);

        // Always place strictly to the RIGHT of the author name element
        el.insertAdjacentElement("afterend", badge);

        if (parentContainer) {
          parentContainer.dataset.rbiLabeled = "true";
        }

        labeledCount++;
      });
    });

    // Notify extension popup if open
    try {
      chrome.runtime.sendMessage({ type: "UPDATE_STATS", count: labeledCount });
    } catch (_) {}

  } catch (err) {
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
