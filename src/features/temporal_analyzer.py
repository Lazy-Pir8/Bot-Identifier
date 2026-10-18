"""
src/features/temporal_analyzer.py — Layer 2: Temporal & Activity Dynamics

Scores a user based on WHEN they post:
  1. Circadian entropy — bots post 24/7, humans have sleep gaps
  2. Inter-arrival time (IAT) regularity — bots use timers, low variance
  3. Burst detection — sudden mass-posting events
  4. Subreddit dispersion speed — context switching too fast to be human

Returns S_temporal in [0.0, 1.0] — higher = more bot-like.
"""
import math
from dataclasses import dataclass, field
from collections import Counter

import numpy as np


KARMA_FARM_SUBREDDITS = {
    "freekarma4u", "karma4free", "freekarma4all", "boostmykarma",
    "freekarma", "getfrekarma", "needkarma", "free_karma", "freekarmaruns",
    "upvote4upvote", "karmafarm", "karma4karma"
}

POLITICAL_ASTROTURF_SUBREDDITS = {
    "politics", "worldnews", "news", "conservative", "politicalcompassmemes",
    "libertarian", "democrats", "republican", "conspiracy", "ukraine",
    "russia", "israel", "palestine", "geopolitics", "europe", "whitepeopletwitter",
    "blackpeopletwitter", "wallstreetbets", "cryptocurrency", "bitcoin", "superstonk"
}


@dataclass
class TemporalScoreDetail:
    circadian_score: float = 0.0
    iat_score: float = 0.0
    burst_score: float = 0.0
    dispersion_score: float = 0.0
    randomness_score: float = 0.0
    karma_farm_score: float = 0.0
    monomania_score: float = 0.0        # Single-subreddit hyper-focus / propaganda
    propaganda_risk_score: float = 0.0  # Concentrated in political/astroturf subs
    shift_pattern_score: float = 0.0    # 24/7 or rigid commercial shift pattern
    final_score: float = 0.0
    notes: list[str] = field(default_factory=list)
    sample_size: int = 0


def _shannon_entropy(counts: list[int]) -> float:
    """Shannon entropy of a discrete distribution."""
    total = sum(counts)
    if total == 0:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    return -sum(p * math.log2(p) for p in probs)


def score_temporal(comments: list[dict]) -> TemporalScoreDetail:
    """
    Compute temporal bot score from a list of comment dicts.
    Each dict must have at minimum: {'created_utc': float, 'subreddit': str}

    Args:
        comments: List of comment dicts, sorted by created_utc (any order).

    Returns:
        TemporalScoreDetail with component scores and final [0,1] score.
    """
    detail = TemporalScoreDetail()

    if len(comments) < 3:
        detail.notes.append("Too few comments for temporal analysis (<3)")
        return detail  # all scores remain 0

    timestamps = sorted(c["created_utc"] for c in comments)
    detail.sample_size = len(timestamps)

    # ── 1. Circadian entropy ──────────────────────────────────────────
    # Bucket into 24 one-hour slots. Human sleep creates a trough.
    # Max entropy = log2(24) ≈ 4.58 (perfectly uniform = bot-like)
    hour_buckets = [0] * 24
    for ts in timestamps:
        import datetime
        hour = datetime.datetime.fromtimestamp(ts, datetime.UTC).hour
        hour_buckets[hour] += 1

    entropy = _shannon_entropy(hour_buckets)
    max_entropy = math.log2(24)  # 4.585
    # Normalize: 0 = human (concentrated), 1 = bot (uniform)
    detail.circadian_score = entropy / max_entropy
    if detail.circadian_score > 0.92:
        detail.notes.append(
            f"Near-uniform 24h activity (entropy={entropy:.2f}/{max_entropy:.2f}) — "
            "no sleep trough detected"
        )

    # Detect 24/7 robotic operation vs human sleep window
    active_hours = sum(1 for h in hour_buckets if h > 0)
    extended_hours = hour_buckets + hour_buckets
    max_sleep_window = 0
    current_gap = 0
    for h in extended_hours:
        if h == 0:
            current_gap += 1
            max_sleep_window = max(max_sleep_window, current_gap)
        else:
            current_gap = 0
    max_sleep_window = min(max_sleep_window, 24)

    if active_hours >= 20 and max_sleep_window < 4 and len(timestamps) >= 15:
        detail.shift_pattern_score = 0.90
        detail.notes.append(
            f"Superhuman 24/7 activity: active in {active_hours}/24 hours with no sleep trough (max rest gap {max_sleep_window}h)"
        )
    elif active_hours >= 18 and max_sleep_window < 5 and len(timestamps) >= 12:
        detail.shift_pattern_score = 0.65
        detail.notes.append(
            f"Unusual 24-hour activity: active in {active_hours}/24 hours (max rest gap {max_sleep_window}h)"
        )

    # ── 2. Inter-arrival time (IAT) regularity ────────────────────────
    # If a bot sleeps for exactly N seconds between actions, IAT std dev is tiny.
    iat_score = 0.0
    if len(timestamps) >= 5:
        iats = np.diff(timestamps)  # seconds between consecutive comments
        # Filter out long gaps (>6 hours) which are normal sleep/work breaks
        active_iats = iats[iats < 21600]
        if len(active_iats) >= 4:
            mean_iat = float(np.mean(active_iats))
            std_iat = float(np.std(active_iats))
            # Coefficient of variation: low CV = suspiciously regular
            cv = std_iat / mean_iat if mean_iat > 0 else 1.0
            if cv < 0.15:
                iat_score = 0.95
                detail.notes.append(
                    f"Extremely regular posting interval: mean={mean_iat:.1f}s, "
                    f"std={std_iat:.1f}s, CV={cv:.3f}"
                )
            elif cv < 0.30:
                iat_score = 0.65
                detail.notes.append(
                    f"Very regular posting interval (CV={cv:.3f})"
                )
            elif cv < 0.50:
                iat_score = 0.30
    detail.iat_score = iat_score

    # ── 3. Burst detection ────────────────────────────────────────────
    # Bots often dump many comments in a short window (batch API calls).
    burst_score = 0.0
    if len(timestamps) >= 10:
        # Slide a 5-minute window and count max density
        window = 300  # 5 minutes
        max_in_window = 0
        for i, t in enumerate(timestamps):
            in_window = sum(1 for t2 in timestamps if t <= t2 <= t + window)
            max_in_window = max(max_in_window, in_window)
        if max_in_window >= 15:
            burst_score = 1.0
            detail.notes.append(f"{max_in_window} comments in a 5-minute window")
        elif max_in_window >= 8:
            burst_score = 0.6
            detail.notes.append(f"{max_in_window} comments in a 5-minute window")
        elif max_in_window >= 5:
            burst_score = 0.3
    detail.burst_score = burst_score

    # ── 4. Subreddit dispersion speed ─────────────────────────────────
    # Switching between completely different subreddits within seconds
    dispersion_score = 0.0
    if len(comments) >= 5:
        sorted_comments = sorted(comments, key=lambda c: c["created_utc"])
        rapid_switches = 0
        for i in range(1, len(sorted_comments)):
            prev = sorted_comments[i - 1]
            curr = sorted_comments[i]
            time_diff = curr["created_utc"] - prev["created_utc"]
            sub_changed = prev["subreddit"] != curr["subreddit"]
            if sub_changed and time_diff < 120:  # different sub within 2 mins
                rapid_switches += 1

        rate = rapid_switches / max(len(comments) - 1, 1)
        if rate > 0.5:
            dispersion_score = 0.85
            detail.notes.append(
                f"Rapid subreddit switching: {rapid_switches} switches in "
                f"{len(comments)} comments"
            )
        elif rate > 0.25:
            dispersion_score = 0.45
    detail.dispersion_score = dispersion_score

    # ── 5. Subreddit Randomness & Topical Coherence ──────────────────
    subs = [c.get("subreddit", "").lower() for c in comments if c.get("subreddit") and c.get("subreddit") != "current_page"]
    if len(subs) >= 5:
        # Karma-farming subreddits check
        farm_hits = set(subs).intersection(KARMA_FARM_SUBREDDITS)
        if farm_hits:
            detail.karma_farm_score = 0.95
            detail.notes.append(
                f"Karma-farming subreddit activity detected: r/{', r/'.join(sorted(farm_hits))}"
            )

        # Subreddit diversity ratio: unique subreddits / total comments
        unique_subs = set(subs)
        diversity_ratio = len(unique_subs) / len(subs)

        # Normalized Shannon entropy of subreddit distribution
        sub_counts = Counter(subs)
        sub_entropy = _shannon_entropy(list(sub_counts.values()))
        max_sub_entropy = math.log2(len(unique_subs)) if len(unique_subs) > 1 else 1.0
        entropy_ratio = sub_entropy / max_sub_entropy if max_sub_entropy > 0 else 0.0

        # Highly scattered posting: posting across random, unrelated subreddits
        if diversity_ratio >= 0.80 and len(subs) >= 6:
            detail.randomness_score = min(entropy_ratio * 0.90, 1.0)
            detail.notes.append(
                f"High subreddit randomness: {len(unique_subs)} unique subreddits in {len(subs)} comments ({diversity_ratio*100:.0f}% scatter)"
            )
        elif diversity_ratio >= 0.50 and len(subs) >= 6:
            detail.randomness_score = min(entropy_ratio * 0.65, 1.0)
            detail.notes.append(
                f"Moderate subreddit scatter: {len(unique_subs)} unique subreddits in {len(subs)} comments ({diversity_ratio*100:.0f}% scatter)"
            )

        # ── 6. Monomania & Political / Astroturfing Cluster Detection ────────
        top_sub, top_count = sub_counts.most_common(1)[0]
        top_ratio = top_count / len(subs)
        is_top_political = top_sub in POLITICAL_ASTROTURF_SUBREDDITS

        if top_ratio >= 0.80 and len(subs) >= 8:
            detail.monomania_score = 0.90 if is_top_political else 0.70
            detail.notes.append(
                f"Hyper-focused single-topic agenda: {top_ratio*100:.0f}% of activity in r/{top_sub}"
                + (" (political/astroturf sub)" if is_top_political else "")
            )
        elif top_ratio >= 0.65 and len(subs) >= 6 and is_top_political:
            detail.monomania_score = 0.60
            detail.notes.append(
                f"Concentrated agenda pushing: {top_ratio*100:.0f}% of comments in r/{top_sub}"
            )

        # Ideological / Political Brigade Cluster Check
        political_comments = [s for s in subs if s in POLITICAL_ASTROTURF_SUBREDDITS]
        political_ratio = len(political_comments) / len(subs)
        if political_ratio >= 0.80 and len(subs) >= 6:
            detail.propaganda_risk_score = 0.85
            detail.notes.append(
                f"Ideological/propaganda cluster: {political_ratio*100:.0f}% of comments in political conflict subreddits"
            )
        elif political_ratio >= 0.60 and len(subs) >= 5:
            detail.propaganda_risk_score = 0.50

    # ── Final score for S_temporal ─────────────────────────────────────
    base_score = (
        0.25 * detail.circadian_score
        + 0.20 * detail.iat_score
        + 0.15 * detail.burst_score
        + 0.15 * detail.dispersion_score
        + 0.10 * detail.randomness_score
        + 0.15 * max(detail.monomania_score, detail.propaganda_risk_score)
    )
    if detail.shift_pattern_score > 0:
        base_score = max(base_score, detail.shift_pattern_score * 0.85)
    if detail.karma_farm_score > 0:
        base_score = max(base_score, detail.karma_farm_score * 0.85)

    # Superhuman burst or dispersion overrides
    if detail.burst_score >= 0.8:
        base_score = max(base_score, detail.burst_score * 0.90)
    if detail.dispersion_score >= 0.8:
        base_score = max(base_score, detail.dispersion_score * 0.85)
    if detail.circadian_score >= 0.88 and detail.sample_size >= 15:
        base_score = max(base_score, detail.circadian_score * 0.75)

    detail.final_score = min(base_score, 1.0)
    return detail
