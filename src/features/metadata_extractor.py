"""
src/features/metadata_extractor.py — Layer 1: Account Metadata Analysis

Scores a Redditor profile on:
  1. Username pattern (default auto-generated Reddit names)
  2. Dormancy gap (old account, sudden activity burst)
  3. Karma asymmetry (link vs comment karma ratio extremes)
  4. Account age (very new accounts are higher risk)
  5. Suspended / no email / history hidden flags

Returns S_meta in [0.0, 1.0] — higher = more bot-like.
"""
import re
import time
from dataclasses import dataclass

from src.collectors.reddit_collector import RedditorProfile


# ── Username patterns ─────────────────────────────────────────────────────────
# Explicit bot indicators in username (e.g., AutoModerator, RemindMeBot, SaveVideoBot, haiku_bot)
_BOT_NAME_RE = re.compile(r"(?i)(^bot|bot$|[-_]bot|\bbot\b|automod)")
# Reddit auto-generates: Adjective_Noun_1234 or Adjective-Noun-1234
_DEFAULT_NAME_RE = re.compile(r"^[A-Za-z]+[_-][A-Za-z]+[_-]?\d{3,5}$")
# Throwaway accounts: throwaway12345, burner_982
_THROWAWAY_RE = re.compile(r"(?i)(throwaway|throw_away|temp|anon|burner)\d*")


@dataclass
class MetaScoreDetail:
    username_score: float = 0.0
    dormancy_score: float = 0.0
    karma_score: float = 0.0
    age_score: float = 0.0
    flag_score: float = 0.0
    final_score: float = 0.0
    notes: list[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


def score_metadata(
    profile: RedditorProfile,
    last_seen_utc: float | None = None,  # most recent comment seen from this user
) -> MetaScoreDetail:
    """
    Compute the metadata bot score for a Redditor.

    Args:
        profile: Populated RedditorProfile from the collector.
        last_seen_utc: Optional timestamp of their most recent observed comment.

    Returns:
        MetaScoreDetail with individual component scores and a final [0,1] score.
    """
    detail = MetaScoreDetail()
    now = time.time()

    # ── 1. Username pattern ───────────────────────────────────────────
    uname = profile.username
    u_score = 0.0
    if _BOT_NAME_RE.search(uname):
        u_score = 0.95
        detail.notes.append(f"Explicit bot indicator in username: {uname}")
    elif _DEFAULT_NAME_RE.match(uname):
        u_score = 0.85  # Almost certainly auto-generated
        detail.notes.append(f"Default-style username pattern matched: {uname}")
    elif _THROWAWAY_RE.search(uname):
        u_score = 0.5
        detail.notes.append("Throwaway/burner naming pattern")
    detail.username_score = u_score

    # ── 2. Dormancy gap ───────────────────────────────────────────────
    # Bot fleets create accounts, age them for months, then activate suddenly.
    d_score = 0.0
    if profile.created_utc > 0 and last_seen_utc:
        account_age_days = (now - profile.created_utc) / 86400
        if profile.recent_comments:
            first_comment_ts = min(
                c["created_utc"] for c in profile.recent_comments
            )
            dormancy_days = (first_comment_ts - profile.created_utc) / 86400
        else:
            dormancy_days = (now - profile.created_utc) / 86400

        if account_age_days > 90 and dormancy_days > 60:
            # Old account that was dormant for >60 days before first comment
            d_score = min(dormancy_days / 365, 1.0) * 0.9
            detail.notes.append(
                f"Sleeper account: {account_age_days:.0f}d old, "
                f"{dormancy_days:.0f}d dormant before activation"
            )
    detail.dormancy_score = d_score

    # ── 3. Karma asymmetry ────────────────────────────────────────────
    k_score = 0.0
    total_karma = profile.comment_karma + profile.link_karma
    if total_karma > 100:
        # Ratio extremes: pure poster (link >> comment) or pure commenter
        # at superhuman speed
        comment_ratio = profile.comment_karma / max(total_karma, 1)
        link_ratio = profile.link_karma / max(total_karma, 1)

        if link_ratio > 0.95 and profile.link_karma > 5000:
            k_score = 0.8
            detail.notes.append(
                f"Extreme link karma dominance: {profile.link_karma} link vs "
                f"{profile.comment_karma} comment"
            )
        elif comment_ratio > 0.98 and profile.comment_karma > 10000:
            # 10k+ comment karma with almost no posts
            # Check if it was rapid: we look at comment count vs account age
            account_age_days = max((now - profile.created_utc) / 86400, 1)
            karma_per_day = profile.comment_karma / account_age_days
            if karma_per_day > 200:
                k_score = 0.75
                detail.notes.append(
                    f"Superhuman comment karma rate: {karma_per_day:.0f} karma/day"
                )
            else:
                k_score = 0.2
    elif total_karma < 10 and profile.created_utc > 0:
        account_age_days = (now - profile.created_utc) / 86400
        if account_age_days < 7:
            k_score = 0.3  # Fresh account — suspicious but not conclusive
            detail.notes.append("Very new account (<7 days old)")
    detail.karma_score = k_score

    # ── 4. Account age ────────────────────────────────────────────────
    age_score = 0.0
    if profile.created_utc > 0:
        account_age_days = (now - profile.created_utc) / 86400
        if account_age_days < 3:
            age_score = 0.7
            detail.notes.append(f"Very new account: {account_age_days:.1f} days old")
        elif account_age_days < 14:
            age_score = 0.4
    detail.age_score = age_score

    # ── 5. Flags ──────────────────────────────────────────────────────
    flag_score = 0.0
    if profile.is_suspended:
        flag_score = 1.0
        detail.notes.append("Account is suspended")
    elif profile.history_hidden:
        flag_score = 0.3
        detail.notes.append("Comment history is hidden/private")
    if not profile.has_verified_email:
        flag_score = min(flag_score + 0.1, 1.0)
        detail.notes.append("No verified email")
    detail.flag_score = flag_score

    # ── Final weighted average for S_meta ─────────────────────────────
    detail.final_score = (
        0.25 * detail.username_score
        + 0.25 * detail.dormancy_score
        + 0.20 * detail.karma_score
        + 0.15 * detail.age_score
        + 0.15 * detail.flag_score
    )
    return detail
