"""
src/features/network_mapper.py — Layer 4: Network & Coordination Graph

Scores a user based on their network and coordination behavior:
  1. Co-occurrence pairs — repeated appearance in identical submissions
  2. Reply-pair & reciprocal echo-chamber detection — mutual back-and-forth loops
  3. Temporal proximity swarming — synchronized comments within 90s of each other
  4. External link & domain syndication — repeated promotional URLs/shorteners
  5. Known-bot / astroturf association propagation

Returns S_network in [0.0, 1.0] — higher = more bot-like.
"""
import re
import time
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from typing import Optional
from pathlib import Path

import aiosqlite
from config.settings import settings

DB_PATH = Path(settings.DB_PATH)

# Known link shorteners, promo redirection services, and spam domains
SUSPICIOUS_SHORTENER_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.me", "discord.gg", "goo.gl", "is.gd",
    "buff.ly", "ow.ly", "linktr.ee", "cutt.ly", "shorturl.at", "rebrand.ly"
}

_URL_DOMAIN_RE = re.compile(
    r"https?://(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:/[^\s)]*)?",
    re.IGNORECASE
)


@dataclass
class NetworkScoreDetail:
    cooccurrence_score: float = 0.0
    reply_pair_score: float = 0.0
    known_bot_association: float = 0.0
    syndication_score: float = 0.0
    swarming_score: float = 0.0
    reciprocal_score: float = 0.0
    final_score: float = 0.0
    flagged_associates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def score_network(username: str, comments: list[dict]) -> NetworkScoreDetail:
    """
    Compute the network coordination score for a user.

    Args:
        username: The Reddit username being analyzed.
        comments: Recent comments and submissions for the user.

    Returns:
        NetworkScoreDetail with component scores and a final [0,1] score.
    """
    detail = NetworkScoreDetail()

    if not comments:
        return detail

    # ── 1. Domain & URL Syndication Analysis ──────────────────────────
    # Bots often syndicate identical external URLs or shorteners across multiple subreddits.
    domain_counts: Counter = Counter()
    shortener_hits = 0
    subs_with_links = set()

    for c in comments:
        body = c.get("body", "")
        if not body:
            continue
        found_domains = _URL_DOMAIN_RE.findall(body)
        for dom in found_domains:
            clean_dom = dom.lower()
            if "reddit.com" not in clean_dom and "redd.it" not in clean_dom and "imgur.com" not in clean_dom:
                domain_counts[clean_dom] += 1
                subs_with_links.add(c.get("subreddit", ""))
                if clean_dom in SUSPICIOUS_SHORTENER_DOMAINS:
                    shortener_hits += 1

    if domain_counts:
        top_domain, top_count = domain_counts.most_common(1)[0]
        if shortener_hits >= 2:
            detail.syndication_score = 0.85
            detail.notes.append(
                f"High-risk URL shortener syndication: {shortener_hits} redirection links detected"
            )
        elif top_count >= 4 and len(subs_with_links) >= 2:
            detail.syndication_score = 0.75
            detail.notes.append(
                f"Repeated domain syndication: '{top_domain}' promoted {top_count} times across {len(subs_with_links)} subreddits"
            )
        elif top_count >= 3:
            detail.syndication_score = 0.45
            detail.notes.append(
                f"Frequent external link sharing: '{top_domain}' posted {top_count} times"
            )

    # ── Database-backed Network & Graph Analysis ──────────────────────
    submission_ids = list(
        {c["submission_id"] for c in comments if c.get("submission_id")}
    )
    user_comment_map = {
        c["id"]: c.get("created_utc", 0) for c in comments if c.get("id")
    }

    if not submission_ids and not user_comment_map:
        detail.final_score = 0.20 * detail.syndication_score
        return detail

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # ── 2. Co-occurrence & Temporal Proximity Swarming ─────────
            if submission_ids:
                placeholders = ",".join("?" * len(submission_ids))
                async with db.execute(
                    f"""SELECT username, submission_id, created_utc
                        FROM comments
                        WHERE submission_id IN ({placeholders})
                          AND username != ?""",
                    submission_ids + [username],
                ) as cursor:
                    rows = await cursor.fetchall()

                co_users: Counter = Counter()
                swarm_hits = 0

                # Map user's submission activity timestamps
                user_sub_times = defaultdict(list)
                for c in comments:
                    sid = c.get("submission_id")
                    if sid:
                        user_sub_times[sid].append(c.get("created_utc", 0))

                for row in rows:
                    other_user = row["username"]
                    sid = row["submission_id"]
                    other_time = row["created_utc"]
                    co_users[other_user] += 1

                    # Check temporal swarming: another account commenting within 90s in same thread
                    if sid in user_sub_times:
                        if any(abs(other_time - ut) < 90 for ut in user_sub_times[sid]):
                            swarm_hits += 1

                if co_users:
                    threshold = max(len(submission_ids) * 0.30, 2)
                    frequent_associates = [
                        u for u, cnt in co_users.items() if cnt >= threshold
                    ]

                    if frequent_associates:
                        bots_in_assoc = []
                        for assoc in frequent_associates:
                            async with db.execute(
                                "SELECT label, bot_score FROM users WHERE username = ?",
                                (assoc,),
                            ) as c:
                                u_row = await c.fetchone()
                                if u_row and u_row["label"] in ("BOT", "SUSPECT"):
                                    bots_in_assoc.append((assoc, u_row["bot_score"]))

                        if bots_in_assoc:
                            detail.known_bot_association = min(
                                len(bots_in_assoc) / max(len(frequent_associates), 1), 1.0
                            )
                            detail.flagged_associates = [b[0] for b in bots_in_assoc]
                            detail.notes.append(
                                f"Co-occurs with {len(bots_in_assoc)} known bots: "
                                + ", ".join(detail.flagged_associates[:3])
                            )

                        detail.cooccurrence_score = min(
                            len(frequent_associates) / 5.0, 1.0
                        ) * 0.7
                        if len(frequent_associates) >= 3:
                            detail.notes.append(
                                f"Co-occurs repeatedly with {len(frequent_associates)} identical accounts"
                            )

                if swarm_hits >= 3:
                    detail.swarming_score = min(swarm_hits / 5.0, 1.0) * 0.85
                    detail.notes.append(
                        f"Temporal thread swarming: synchronized activity within 90s across {swarm_hits} interactions"
                    )

            # ── 3. Reply-Pair & Reciprocal Echo-Chamber Detection ───────
            if user_comment_map:
                reply_targets = [f"t1_{cid}" for cid in user_comment_map.keys()]
                placeholders_reply = ",".join("?" * len(reply_targets))
                async with db.execute(
                    f"""SELECT username, created_utc, parent_id
                        FROM comments
                        WHERE parent_id IN ({placeholders_reply})
                          AND username != ?""",
                    reply_targets + [username],
                ) as cursor:
                    reply_rows = await cursor.fetchall()

                reply_users: Counter = Counter(r["username"] for r in reply_rows)
                rapid_repliers = [
                    (u, cnt) for u, cnt in reply_users.items() if cnt >= 2
                ]

                if rapid_repliers:
                    detail.reply_pair_score = min(len(rapid_repliers) / 3.0, 1.0) * 0.8
                    detail.notes.append(
                        f"Consistent reply pairs: {len(rapid_repliers)} accounts repeatedly reply to this user"
                    )

                    # Check for reciprocal loops: did this user also reply to those repliers?
                    top_replier = rapid_repliers[0][0]
                    async with db.execute(
                        """SELECT count(*) as c FROM comments
                           WHERE username = ? AND body LIKE ?""",
                        (username, f"%{top_replier}%"),
                    ) as c:
                        recip_row = await c.fetchone()
                        if recip_row and recip_row["c"] >= 2:
                            detail.reciprocal_score = 0.75
                            detail.notes.append(
                                f"Reciprocal echo-chamber: Mutual validation pattern detected with u/{top_replier}"
                            )

    except Exception:
        pass

    # ── Final composite weighted score for S_network ──────────────────
    detail.final_score = min(
        0.25 * detail.cooccurrence_score
        + 0.20 * detail.reply_pair_score
        + 0.20 * detail.known_bot_association
        + 0.20 * detail.syndication_score
        + 0.15 * max(detail.swarming_score, detail.reciprocal_score),
        1.0
    )

    if detail.syndication_score >= 0.75:
        detail.final_score = max(detail.final_score, detail.syndication_score * 0.80)
    if detail.known_bot_association >= 0.70:
        detail.final_score = max(detail.final_score, detail.known_bot_association * 0.80)

    return detail
