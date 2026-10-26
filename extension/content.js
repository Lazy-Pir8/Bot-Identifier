/**
 * extension/content.js — Reddit DOM Observer & Badge Injector
 * 
 * Dynamically finds author handles across Reddit (Old Reddit, New Reddit & Shreddit),
 * requests bot ratings from the local API server (http://127.0.0.1:8000),
 * and injects a single color-coded badge strictly next to the author's username.
 */

const API_SERVER = "http://127.0.0.1:8000";
const PENDING_QUEUE = new Map(); // username -> { elements: [], commentBody: "", commentId: "" }
const CLIENT_CACHE = new Map();  // username -> result data (in-memory instant cache)
let debounceTimer = null;

// Track scanned stats for the extension popup
let labeledCount = 0;

// Respond to popup status queries
try {
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === "GET_COUNT") {
      sendResponse({ count: labeledCount });
    }
  });
} catch (_) {}

// CSS styles injected into Web Component shadow roots so badges are fully styled
const SHADOW_BADGE_CSS = `
  .rbi-badge {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-width: 26px !important;
    height: 15px !important;
    padding: 0 4px !important;
    margin-left: 5px !important;
    margin-right: 4px !important;
    vertical-align: middle !important;
    align-self: center !important;
    flex-shrink: 0 !important;
    border-radius: 9999px !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
    font-size: 10px !important;
    font-weight: 700 !important;
    letter-spacing: 0.02em !important;
    color: #ffffff !important;
    line-height: 15px !important;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.4) !important;
    cursor: pointer !important;
    user-select: none !important;
    white-space: nowrap !important;
    transition: transform 0.12s ease, box-shadow 0.12s ease !important;
    z-index: 10 !important;
    visibility: visible !important;
    opacity: 1 !important;
    box-sizing: border-box !important;
  }
  .rbi-badge:hover {
    transform: scale(1.14);
  }
  .rbi-tier-1 { background: linear-gradient(135deg, #10b981, #059669) !important; box-shadow: 0 1px 3px rgba(16, 185, 129, 0.35) !important; }
  .rbi-tier-2 { background: linear-gradient(135deg, #14b8a6, #0d9488) !important; box-shadow: 0 1px 3px rgba(20, 184, 166, 0.35) !important; }
  .rbi-tier-3 { background: linear-gradient(135deg, #06b6d4, #0284c7) !important; box-shadow: 0 1px 3px rgba(6, 182, 212, 0.35) !important; }
  .rbi-tier-4 { background: linear-gradient(135deg, #eab308, #ca8a04) !important; box-shadow: 0 1px 3px rgba(234, 179, 8, 0.40) !important; }
  .rbi-tier-5 { background: linear-gradient(135deg, #f97316, #ea580c) !important; box-shadow: 0 1px 4px rgba(249, 115, 22, 0.45) !important; }
  .rbi-tier-6 { background: linear-gradient(135deg, #f43f5e, #e11d48) !important; box-shadow: 0 1px 5px rgba(244, 63, 94, 0.50) !important; }
  .rbi-tier-7 { background: linear-gradient(135deg, #ff1744, #d50000) !important; box-shadow: 0 1px 6px rgba(255, 23, 68, 0.60) !important; }
  .rbi-high-bot { animation: rbi-pulse 2s infinite ease-in-out; }
  @keyframes rbi-pulse {
    0%, 100% { box-shadow: 0 0 5px rgba(255, 23, 68, 0.5); }
    50% { box-shadow: 0 0 12px rgba(255, 23, 68, 0.95); }
  }
`;

function ensureShadowStyles(root) {
  if (!root || root === document) return;
  if (root.querySelector && !root.querySelector("#rbi-shadow-styles")) {
    const style = document.createElement("style");
    style.id = "rbi-shadow-styles";
    style.textContent = SHADOW_BADGE_CSS;
    root.appendChild(style);
  }
}

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
  // 1. Shreddit custom element attribute on self
  if (element.hasAttribute("author")) {
    const a = element.getAttribute("author");
    if (a && a !== "[deleted]" && a !== "[removed]") return a.replace(/^u\//i, "").trim();
  }

  // 2. Check href on element or inside element
  const href = element.getAttribute("href") || element.querySelector?.("a[href*='/user/'], a[href*='/u/']")?.getAttribute("href") || "";
  const match = href.match(/\/(?:user|u)\/([A-Za-z0-9_\-]+)/i);
  if (match) {
    const name = match[1];
    const systemNames = ["me", "settings", "null", "undefined", "notifications", "submit"];
    if (!systemNames.includes(name.toLowerCase())) {
      return name;
    }
  }

  // 3. Check ancestor shreddit container author attribute
  const shredditParent = element.closest?.("shreddit-comment[author], shreddit-post[author]");
  if (shredditParent) {
    const a = shredditParent.getAttribute("author");
    if (a && a !== "[deleted]" && a !== "[removed]") return a.replace(/^u\//i, "").trim();
  }

  // 4. Fallback to clean inner text if formatted as u/Name or Name
  const text = (element.innerText || "").trim().replace(/^u\//i, "");
  const firstWord = text.split(/[\s\n•·]+/)[0];
  if (firstWord && /^[A-Za-z0-9_\-]{3,20}$/.test(firstWord)) {
    return firstWord;
  }

  return null;
}

/**
 * Validate that an element is strictly the author's name display element
 * (and NOT an avatar, icon, or mention inside comment body).
 */
function isValidAuthorElement(el, username) {
  if (!el || !el.isConnected) return false;

  // Never accept custom component containers themselves as the badge anchor
  const tag = (el.tagName || "").toLowerCase();
  if (tag === "shreddit-comment" || tag === "shreddit-post" || tag === "article") {
    return false;
  }

  // 1. Must NOT be an avatar, icon, or inside an avatar/icon container
  if (
    el.closest('[slot*="Avatar"], [slot*="avatar"], [slot*="avitar"], [data-testid*="avatar" i], [data-testid*="icon" i], [aria-label*="avatar" i], .avatar') ||
    el.matches('[slot*="Avatar"], [slot*="avatar"], [slot*="avitar"], [data-testid*="avatar" i], [aria-label*="avatar" i]')
  ) {
    return false;
  }

  // 2. Reject if element contains image/svg and has no visible text
  if (el.querySelector('faceplate-img, img, svg, faceplate-partial') && !el.innerText.trim()) {
    return false;
  }

  // 3. Must NOT be inside comment text body or markdown paragraphs (which are user mentions like u/someone, not authors)
  if (el.closest('[data-testid="comment-body"], .usertext-body, [slot="comment"], [slot="comment-body"], div[id*="-comment-rtjson-content"], .md p')) {
    return false;
  }

  // 4. Must NOT be a link to generic user profile actions like settings or message
  const href = el.getAttribute("href") || "";
  if (href.includes("/message/") || href.includes("/settings/")) {
    return false;
  }

  // 5. Must contain text matching the username if it is an anchor/span display element
  const text = (el.innerText || "").trim().toLowerCase();
  if (text) {
    if (!text.includes(username.toLowerCase())) {
      return false;
    }
  }

  // 6. Check if parent comment/post or immediate context ALREADY has a badge or was labeled
  const parentContainer = el.closest('shreddit-comment, shreddit-post, div.comment, [data-testid="comment"], [data-testid="post-container"], tr.thing');
  if (parentContainer) {
    if (parentContainer.dataset.rbiLabeled === "true" ||
        parentContainer.querySelector('.rbi-badge') ||
        parentContainer.shadowRoot?.querySelector('.rbi-badge')) {
      return false;
    }
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
 * Inject single badge strictly next to the author handle element
 */
function injectBadge(el, result) {
  if (!el || !el.isConnected) return;

  // Check parent container
  const parentContainer = el.closest('shreddit-comment, shreddit-post, div.comment, [data-testid="comment"], [data-testid="post-container"], tr.thing');
  if (parentContainer) {
    if (parentContainer.dataset.rbiLabeled === "true" ||
        parentContainer.querySelector('.rbi-badge') ||
        parentContainer.shadowRoot?.querySelector('.rbi-badge')) {
      return;
    }
  }

  // Determine the exact insertion target right next to the username
  // If the anchor is wrapped by a hovercard, placing right after the anchor or hovercard ensures correct flow
  let target = el;
  if (el.parentElement && el.parentElement.tagName.toLowerCase() === "faceplate-hovercard") {
    target = el.parentElement;
  }

  // Prevent duplicate badge directly next to target
  if (target.nextElementSibling && target.nextElementSibling.classList.contains("rbi-badge")) {
    return;
  }

  // If inside a shadow root, ensure badge styles are injected into that shadow root
  const root = target.getRootNode();
  if (root && root !== document) {
    ensureShadowStyles(root);
  }

  const badge = createBadge(result);

  // Position strictly to the RIGHT of the username handle
  target.insertAdjacentElement("afterend", badge);

  if (parentContainer) {
    parentContainer.dataset.rbiLabeled = "true";
  }

  labeledCount++;
}

/**
 * Scan DOM for unparsed author elements
 */
function scanAuthors(isInitial = false) {
  let added = 0;

  function registerCandidate(el, username) {
    if (!el || !el.isConnected) return;
    if (!isValidAuthorElement(el, username)) return;

    // Check if container already labeled
    const parentContainer = el.closest('shreddit-comment, shreddit-post, div.comment, [data-testid="comment"], [data-testid="post-container"], tr.thing');
    if (parentContainer) {
      if (parentContainer.dataset.rbiLabeled === "true" ||
          parentContainer.querySelector('.rbi-badge') ||
          parentContainer.shadowRoot?.querySelector('.rbi-badge')) {
        return;
      }
    }

    el.dataset.rbiChecked = "true";

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
  }

  // 1. Scan direct light-DOM author elements across all Reddit versions
  const selectors = [
    'faceplate-tracker[source="post_author"] a:not([data-rbi-checked])',
    'faceplate-tracker[source="comment_author"] a:not([data-rbi-checked])',
    '[slot="authorName"] a:not([data-rbi-checked])',
    '[slot="authorName"]:not([data-rbi-checked])',
    'a[data-testid="comment_author"]:not([data-rbi-checked])',
    'a[data-testid="post_author"]:not([data-rbi-checked])',
    'a[data-testid="author-link"]:not([data-rbi-checked])',
    'a.author:not([data-rbi-checked])',
    'a[href*="/user/"]:not([data-rbi-checked])',
    'a[href*="/u/"]:not([data-rbi-checked])'
  ];

  document.querySelectorAll(selectors.join(", ")).forEach((el) => {
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "shreddit-comment" || tag === "shreddit-post" || tag === "article") return;

    const username = extractUsername(el);
    if (!username) return;

    registerCandidate(el, username);
  });

  // 2. Scan Shreddit custom components to pierce shadowRoot for encapsulated author links
  document.querySelectorAll('shreddit-comment, shreddit-post').forEach((container) => {
    if (container.dataset.rbiLabeled === "true") return;

    const rawAuthor = container.getAttribute("author");
    if (!rawAuthor || rawAuthor === "[deleted]" || rawAuthor === "[removed]") return;

    const cleanUsername = rawAuthor.replace(/^u\//i, "").trim();

    // First check light DOM inside container
    const lightAuthor = container.querySelector(
      'faceplate-tracker[source="comment_author"] a:not([data-rbi-checked]), faceplate-tracker[source="post_author"] a:not([data-rbi-checked]), [slot="authorName"] a:not([data-rbi-checked]), [slot="authorName"]:not([data-rbi-checked])'
    );
    if (lightAuthor) {
      registerCandidate(lightAuthor, cleanUsername);
      return;
    }

    // Next check open shadowRoot inside container
    if (container.shadowRoot) {
      const shadowAuthor = container.shadowRoot.querySelector(
        'a.author:not([data-rbi-checked]), a[href*="/user/"]:not([data-rbi-checked]):not([slot*="avatar"]):not([slot*="avitar"]):not([aria-label*="avatar"])'
      );
      if (shadowAuthor) {
        registerCandidate(shadowAuthor, cleanUsername);
        return;
      }
    }
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
    let data = null;

    // Fast Step A: Request via Background Service Worker (immune to page CSP & Private Network Access restrictions)
    try {
      const bgResp = await new Promise((resolve) => {
        chrome.runtime.sendMessage(
          {
            type: "SCAN_USERS",
            payload: {
              users: usersPayload,
              usernames: usernamesPayload,
            },
          },
          (res) => {
            if (chrome.runtime.lastError || !res || !res.ok) {
              resolve(null);
            } else {
              resolve(res.data);
            }
          }
        );
      });
      if (bgResp && bgResp.results) {
        data = bgResp;
      }
    } catch (_) {}

    // Fallback Step B: Direct fetch to local server
    if (!data) {
      const resp = await fetch(`${API_SERVER}/api/scan-users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          users: usersPayload,
          usernames: usernamesPayload,
        }),
      });

      if (resp.ok) {
        data = await resp.json();
      }
    }

    if (!data) return;

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
