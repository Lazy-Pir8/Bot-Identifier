/**
 * extension/content.js — Reddit DOM Observer & Badge Injector
 * 
 * Dynamically finds author handles across Reddit (Old Reddit, New Reddit & Shreddit),
 * requests bot ratings from the local API server (http://127.0.0.1:8000),
 * and injects a single color-coded badge strictly to the right of the author's name.
 */

const API_SERVER = "http://127.0.0.1:8000";
const PENDING_QUEUE = new Map(); // username -> Array of DOM elements
const CLIENT_CACHE = new Map();  // username -> result data (in-memory instant cache)
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

function formatTierLabel(tier) {
  if (tier === "TIER_1_DETERMINISTIC") return "Tier 1: Deterministic";
  if (tier === "TIER_2_COMMUNITY_WATCHLIST") return "Tier 2: Watchlist";
  if (tier === "TIER_3_BEHAVIORAL_FORENSICS") return "Tier 3: Forensics";
  return "";
}

function showTooltip(badgeEl, data) {
  const tooltip = getOrCreateGlobalTooltip();
  const scorePct = Math.round((data.score || 0) * 100);
  const scores = data.scores || {};
  const weights = data.dynamic_weights || null;
  const tierName = formatTierLabel(data.evidence_tier);

  const verdictSummary = data.verdict_summary || (
    scorePct >= 65 ? "Automated bot profile identified based on behavioral patterns." :
    (scorePct >= 40 ? "Suspicious activity pattern with mixed behavioral signals." : "Authentic organic human profile.")
  );

  const primaryFactor = data.primary_factor || (
    scorePct >= 65 ? "Temporal Activity Dynamics" : "Organic Human Profile"
  );

  const reasons = (data.specific_reasons && data.specific_reasons.length > 0)
    ? data.specific_reasons
    : (data.notes && data.notes.length > 0 ? data.notes : []);

  let reasonsHtml = "";
  if (reasons.length > 0) {
    reasonsHtml = `
      <div class="rbi-tooltip-section-title">Key Observations & Evidence</div>
      <div class="rbi-tooltip-notes">
        ${reasons.slice(0, 5).map(r => `
          <div class="rbi-tooltip-note-item">
            <span class="rbi-bullet">•</span>
            <span>${escapeHtml(r)}</span>
          </div>
        `).join("")}
      </div>
    `;
  }

  const tierBadgeHtml = tierName ? `<span class="rbi-tooltip-tier">${escapeHtml(tierName)}</span>` : "";

  function formatLayer(score, weight) {
    const sStr = (score || 0).toFixed(2);
    if (weight !== null && weight !== undefined) {
      const wPct = Math.round(weight * 100);
      return `${sStr} <span class="rbi-weight">(${wPct}%)</span>`;
    }
    return sStr;
  }

  tooltip.innerHTML = `
    <div class="rbi-tooltip-header">
      <span class="rbi-tooltip-title">u/${escapeHtml(data.username)}</span>
      <span class="rbi-tooltip-score" style="color: ${data.color || '#fff'};">${scorePct}% Bot Score</span>
    </div>
    <div class="rbi-tooltip-tier-row">
      ${tierBadgeHtml}
      <span class="rbi-tooltip-driver">${escapeHtml(primaryFactor)}</span>
    </div>
    <div class="rbi-tooltip-verdict">
      ${escapeHtml(verdictSummary)}
    </div>
    <div class="rbi-tooltip-section-title">Forensic Layer Breakdown</div>
    <div class="rbi-tooltip-breakdown">
      <div class="rbi-breakdown-col"><span class="rbi-layer-name">Meta:</span> ${formatLayer(scores.meta, weights ? weights.meta : null)}</div>
      <div class="rbi-breakdown-col"><span class="rbi-layer-name">Temporal:</span> ${formatLayer(scores.temporal, weights ? weights.temporal : null)}</div>
      <div class="rbi-breakdown-col"><span class="rbi-layer-name">Linguistic:</span> ${formatLayer(scores.linguistic, weights ? weights.linguistic : null)}</div>
      <div class="rbi-breakdown-col"><span class="rbi-layer-name">Network:</span> ${formatLayer(scores.network, weights ? weights.network : null)}</div>
    </div>
    ${reasonsHtml}
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
 * Extract visible comment text on the current page.
 * Crucial for immediate analysis when user's Reddit profile history is hidden.
 */
function extractCommentBody(element) {
  const container = element.closest('shreddit-comment, div[data-testid="comment"], div.comment, tr.thing');
  if (!container) return "";

  // 1. Shreddit comment slot or rtjson content
  const shredditBody = container.querySelector('[slot="comment"], div[id*="-comment-rtjson-content"]');
  if (shredditBody && shredditBody.innerText.trim()) {
    return shredditBody.innerText.trim();
  }

  // 2. data-testid="comment-body"
  const testIdBody = container.querySelector('[data-testid="comment-body"], [data-testid="comment-body-wrapper"]');
  if (testIdBody && testIdBody.innerText.trim()) {
    return testIdBody.innerText.trim();
  }

  // 3. Old Reddit / markdown body
  const mdBody = container.querySelector('.usertext-body, .md');
  if (mdBody && mdBody.innerText.trim()) {
    return mdBody.innerText.trim();
  }

  return "";
}

/**
 * Extract comment ID on the current page if available
 */
function extractCommentId(element) {
  const container = element.closest('shreddit-comment, div[data-testid="comment"], div.comment, tr.thing');
  if (!container) return "";

  if (container.getAttribute("thingid")) return container.getAttribute("thingid");
  if (container.getAttribute("id")) return container.getAttribute("id");
  if (container.dataset && container.dataset.fullname) return container.dataset.fullname;
  return "";
}

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
 * Inject single badge strictly next to author element
 */
function injectBadge(el, result) {
  if (!el || !el.isConnected) return;

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
}

/**
 * Scan DOM for unparsed author elements
 */
function scanAuthors(isInitial = false) {
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

    // Fast Step 0: In-memory client cache lookup (0.0ms instant render)
    if (CLIENT_CACHE.has(username)) {
      injectBadge(el, CLIENT_CACHE.get(username));
      return;
    }

    const commentBody = extractCommentBody(el);
    const commentId = extractCommentId(el);

    if (!PENDING_QUEUE.has(username)) {
      PENDING_QUEUE.set(username, {
        elements: [],
        commentBody: commentBody || "",
        commentId: commentId || "",
      });
    }
    const entry = PENDING_QUEUE.get(username);
    entry.elements.push(el);
    if (!entry.commentBody && commentBody) {
      entry.commentBody = commentBody;
      entry.commentId = commentId;
    }
    added++;
  });

  if (added > 0) {
    scheduleBatchFetch(isInitial);
  }
}

/**
 * Debounce batch API request (ultra-low latency 75ms or 15ms initial)
 */
function scheduleBatchFetch(immediate = false) {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(processBatchQueue, immediate ? 15 : 75);
}

/**
 * Send queued usernames to local backend and inject badges
 */
async function processBatchQueue() {
  if (PENDING_QUEUE.size === 0) return;

  const currentEntries = Array.from(PENDING_QUEUE.entries()).slice(0, 50);
  const elementsToUpdate = new Map();
  const usersPayload = [];
  const usernamesPayload = [];

  currentEntries.forEach(([username, data]) => {
    elementsToUpdate.set(username, data.elements);
    usersPayload.push({
      username: username,
      comment_body: data.commentBody || "",
      comment_id: data.commentId || "",
    });
    usernamesPayload.push(username);
    PENDING_QUEUE.delete(username);
  });

  try {
    const resp = await fetch(`${API_SERVER}/api/scan-users`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        users: usersPayload,
        usernames: usernamesPayload,
      }),
    });

    if (!resp.ok) return;

    const data = await resp.json();
    const results = data.results || {};

    elementsToUpdate.forEach((elements, username) => {
      const result = results[username];
      if (!result) return;

      // Cache locally in tab memory for instant reuse
      CLIENT_CACHE.set(username, result);

      elements.forEach((el) => {
        injectBadge(el, result);
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

// Immediate initial scan for visible authors
scanAuthors(true);

// Watch for dynamically loaded comments (infinite scroll / expanding threads)
const observer = new MutationObserver(() => {
  scanAuthors(false);
});

observer.observe(document.body, {
  childList: true,
  subtree: true,
});
