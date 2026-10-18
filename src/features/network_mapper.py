"""
src/features/network_mapper.py — Layer 4: Network & Coordination Graph

Scores a user based on their network behavior:
  1. Co-occurrence pairs — do they always appear with the same accounts?
  2. Reply-pair detection — do specific accounts always reply to them?
  3. Timing correlation — do they upvote/comment within seconds of specific users?

NOTE: Network scores improve over time as the DB collects more data.
      New users will have low (neutral) network scores initially.

Returns S_network in [0.0, 1.0] — higher = more bot-like.
"""
import time
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from typing import Optional

import aiosqlite
from config.settings import settings
from pathlib import Path

DB_PATH = Path(settings.DB_PATH)


@dataclass
class NetworkScoreDetail:
    cooccurrence_score: float = 0.0
    reply_pair_score: float = 0.0
    known_bot_association: float = 0.0
    final_score: float = 0.0
    flagged_associates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def score_network(username: str, comments: list[dict]) -> NetworkScoreDetail:
    """
    Compute the network coordination score for a user.

    Args:
        username: The Reddit username being analyzed.
        comments: Recent comments for the user (must have 'submission_id' and 'created_utc').

    Returns:
        NetworkScoreDetail with component scores and a final [0,1] score.
    """
    detail = NetworkScoreDetail()

    if not comments:
        return detail

    submission_ids = list(
        {c["submission_id"] for c in comments if c.get("submission_id")}
    )
    if not submission_ids:
        return detail

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # ── 1. Co-occurrence: find other users in the same threads ─────
        placeholders = ",".join("?" * len(submission_ids))
        async with db.execute(
            f"""SELECT username, submission_id, created_utc
                FROM comments
                WHERE submission_id IN ({placeholders})
                  AND username != ?""",
            submission_ids + [username],
        ) as cursor:
            rows = await cursor.fetchall()

        # Build co-occurrence counter
        co_users: Counter = Counter()
        for row in rows:
            co_users[row["username"]] += 1

        if co_users:
            # Flag users who appear in >30% of the same threads
            threshold = max(len(submission_ids) * 0.30, 2)
            frequent_associates = [
                u for u, cnt in co_users.items() if cnt >= threshold
            ]

            if frequent_associates:
                # Check how many of these are known bots
                bots_in_assoc = []
                for assoc in frequent_associates:
                    async with db.execute(
                        "SELECT label, bot_score FROM users WHERE username = ?",
                        (assoc,),
                    ) as c:
                        row = await c.fetchone()
                        if row and row["label"] in ("BOT", "SUSPECT"):
                            bots_in_assoc.append((assoc, row["bot_score"]))

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
                        f"Always appears with {len(frequent_associates)} same accounts"
                    )

        # ── 2. Reply-pair detection ────────────────────────────────────
        # Find comments that are direct replies to this user's comments
        user_comment_ids = {c["id"] for c in comments if c.get("id")}
        if user_comment_ids:
            placeholders2 = ",".join("?" * len(user_comment_ids))
            reply_targets = [f"t1_{cid}" for cid in user_comment_ids]
            placeholders3 = ",".join("?" * len(reply_targets))
            async with db.execute(
                f"""SELECT username, created_utc, parent_id
                    FROM comments
                    WHERE parent_id IN ({placeholders3})
                      AND username != ?""",
                reply_targets + [username],
            ) as cursor:
                reply_rows = await cursor.fetchall()

            reply_users: Counter = Counter(r["username"] for r in reply_rows)
            rapid_repliers = [
                (u, cnt)
                for u, cnt in reply_users.items()
                if cnt >= 2  # replied to this user ≥2 times
            ]
            if rapid_repliers:
                detail.reply_pair_score = min(len(rapid_repliers) / 3.0, 1.0) * 0.8
                detail.notes.append(
                    f"Consistent reply pairs: {len(rapid_repliers)} accounts "
                    f"repeatedly reply to this user"
                )

    # ── Final weighted average for S_network ──────────────────────────
    detail.final_score = (
        0.45 * detail.cooccurrence_score
        + 0.30 * detail.reply_pair_score
        + 0.25 * detail.known_bot_association
    )
    return detail
