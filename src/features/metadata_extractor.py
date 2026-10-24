"""
src/features/metadata_extractor.py — Layer 1: Account Metadata Analysis

Scores a Redditor profile on:
  1. Username pattern (explicit bot names, throwaway patterns, neutral default patterns)
  2. Dormancy gap & activation velocity (distinguishes natural human lurkers from sleeper bot fleets)
  3. Karma asymmetry (link vs comment karma ratio extremes)
  4. Account age (very new accounts are higher risk)
  5. Suspended / no email / history hidden flags

Returns S_meta in [0.0, 1.0] — higher = more bot-like.
"""
import re
import time
from dataclasses import dataclass, field

from src.collectors.reddit_collector import RedditorProfile


# ── Username patterns ─────────────────────────────────────────────────────────
# Explicit bot indicators in username (e.g., AutoModerator, RemindMeBot, SaveVideoBot, haiku_bot)
_BOT_NAME_RE = re.compile(r"(?i)(^bot|bot$|[-_]bot|\bbot\b|automod)")

# Reddit auto-generates: Adjective_Noun_1234 or Adjective-Noun-1234
# Very common for authentic modern users signing in via Google, Apple, or mobile app
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
    is_custom_username: bool = False
    is_default_username: bool = False
    notes: list[str] = field(default_factory=list)


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
        # Default suggested Reddit handle (e.g. Adjective-Noun-1234)
        # Assigned by Reddit to millions of authentic humans via Google/Apple/Mobile signup.
        # Mild prior only (0.20), never a standalone bot indicator.
        u_score = 0.20
        detail.is_default_username = True
        detail.notes.append(f"Default Reddit handle pattern (weak signal, common among mobile/OAuth humans): {uname}")
    elif _THROWAWAY_RE.search(uname):
        u_score = 0.35
        detail.notes.append("Throwaway/burner naming pattern")
    else:
        # Custom chosen handle — authentic organic human signal
        u_score = 0.0
        detail.is_custom_username = True
        detail.notes.append(f"Custom chosen username handle: {uname}")
    detail.username_score = u_score

    # ── 2. Dormancy gap & activation velocity ────────────────────────
    # Distinguishes genuine sleeper bot fleets from normal human lurkers & hiatuses.
    # A sleeper bot is an account created in batch, aged silently, and then
    # suddenly activates with high burst velocity or template spam.
    # Normal human behavior includes account creation for reading/lurking,
    # or taking multi-month breaks from social media.
    d_score = 0.0
    if profile.created_utc > 0:
        account_age_days = (now - profile.created_utc) / 86400
        comments = profile.recent_comments or []

        # Sampling Truncation Guard:
        # If the account has established karma (>150) or our comment sample reaches
        # the collector limit (>=20 comments), the oldest comment in our sample is strictly
        # a sample boundary cutoff, NOT the account's historical first comment!
        has_established_history = (
            len(comments) >= 20
            or profile.comment_karma > 150
            or profile.link_karma > 150
        )

        if has_established_history:
            # Check for dormancy strictly WITHIN the sampled timeline (e.g. gaps between comments)
            if len(comments) >= 2:
                sorted_ts = sorted(c["created_utc"] for c in comments)
                gaps = [
                    (sorted_ts[i] - sorted_ts[i - 1]) / 86400
                    for i in range(1, len(sorted_ts))
                ]
                max_gap = max(gaps) if gaps else 0
                if max_gap > 90:
                    # Account took a 90+ day hiatus within the sample
                    # Check activation velocity after the gap
                    post_gap_comments = sum(1 for ts in sorted_ts if ts >= sorted_ts[-1] - 7 * 86400)
                    if post_gap_comments >= 35:  # superhuman 5+ comments/day continuously
                        d_score = 0.60
                        detail.notes.append(
                            f"Suspicious post-hiatus burst: {max_gap:.0f}d inactive gap followed by high velocity"
                        )
                    else:
                        detail.notes.append(
                            f"Normal human hiatus: {max_gap:.0f}d break followed by organic activity pace"
                        )
        else:
            # Truly sparse account: total lifetime karma is low and <=20 comments total
            if comments:
                first_comment_ts = min(c["created_utc"] for c in comments)
                dormancy_days = (first_comment_ts - profile.created_utc) / 86400
                active_span_days = max((now - first_comment_ts) / 86400, 0.5)
                activation_rate = len(comments) / active_span_days  # comments per day since waking up

                if account_age_days > 90 and dormancy_days > 60:
                    if activation_rate >= 8.0:
                        # Aged dormant account suddenly waking up with high daily burst rate
                        d_score = min(dormancy_days / 365.0, 1.0) * 0.80
                        detail.notes.append(
                            f"Sleeper account: {account_age_days:.0f}d old, {dormancy_days:.0f}d dormant "
                            f"before high-velocity activation ({activation_rate:.1f} comments/day)"
                        )
                    elif activation_rate >= 3.0:
                        d_score = 0.30
                        detail.notes.append(
                            f"Moderate post-dormancy activity: {activation_rate:.1f} comments/day"
                        )
                    else:
                        # Low, organic human rate (e.g. 1 comment every few days)
                        d_score = 0.05
                        detail.notes.append(
                            f"Organic human account lifecycle: {dormancy_days:.0f}d early lurking/hiatus, "
                            f"organic low-frequency posting ({activation_rate:.1f} comments/day)"
                        )
    detail.dormancy_score = d_score

    # ── 3. Karma asymmetry ────────────────────────────────────────────
    k_score = 0.0
    total_karma = profile.comment_karma + profile.link_karma
    if total_karma > 100:
        comment_ratio = profile.comment_karma / max(total_karma, 1)
        link_ratio = profile.link_karma / max(total_karma, 1)

        if link_ratio > 0.95 and profile.link_karma > 5000:
            k_score = 0.8
            detail.notes.append(
                f"Extreme link karma dominance: {profile.link_karma} link vs "
                f"{profile.comment_karma} comment"
            )
        elif comment_ratio > 0.98 and profile.comment_karma > 10000:
            account_age_days = max((now - profile.created_utc) / 86400, 1)
            karma_per_day = profile.comment_karma / account_age_days
            if karma_per_day > 200:
                k_score = 0.75
                detail.notes.append(
                    f"Superhuman comment karma rate: {karma_per_day:.0f} karma/day"
                )
            else:
                k_score = 0.15
    elif total_karma < 10 and profile.created_utc > 0:
        account_age_days = (now - profile.created_utc) / 86400
        if account_age_days < 7:
            k_score = 0.3
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
            age_score = 0.35
    detail.age_score = age_score

    # ── 5. Flags ──────────────────────────────────────────────────────
    flag_score = 0.0
    if profile.is_suspended:
        flag_score = 1.0
        detail.notes.append("Account is suspended")
    elif profile.history_hidden:
        flag_score = 0.25
        detail.notes.append("Comment history is hidden/private")
    if not profile.has_verified_email:
        flag_score = min(flag_score + 0.08, 1.0)
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
