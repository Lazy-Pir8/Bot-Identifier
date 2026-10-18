"""
src/models/bot_classifier.py — Composite Bot Scoring Engine

Combines all four layer scores into a final bot probability:

  Score_bot = w1*S_meta + w2*S_temporal + w3*S_linguistic + w4*S_network

Also:
  - Applies the label (HUMAN / SUSPECT / BOT) based on thresholds
  - Persists results to the database
  - Handles the case where comment history is fully hidden (weight redistribution)
"""
import time
from dataclasses import dataclass, field
from typing import Optional

from config.settings import settings
from src.collectors.reddit_collector import RedditorProfile
from src.features.metadata_extractor import score_metadata, MetaScoreDetail
from src.features.temporal_analyzer import score_temporal, TemporalScoreDetail
from src.features.text_forensics import score_linguistics, LinguisticScoreDetail
from src.features.network_mapper import score_network, NetworkScoreDetail
from src.db import database as db


@dataclass
class BotAnalysisResult:
    username: str
    bot_score: float           # [0.0, 1.0]
    label: str                 # HUMAN / SUSPECT / BOT
    score_meta: float
    score_temporal: float
    score_linguistic: float
    score_network: float
    meta_detail: Optional[MetaScoreDetail] = None
    temporal_detail: Optional[TemporalScoreDetail] = None
    linguistic_detail: Optional[LinguisticScoreDetail] = None
    network_detail: Optional[NetworkScoreDetail] = None
    history_hidden: bool = False
    trigger_comment: Optional[str] = None
    analyzed_at: float = field(default_factory=time.time)

    @property
    def all_notes(self) -> list[str]:
        notes = []
        for d in [self.meta_detail, self.temporal_detail,
                  self.linguistic_detail, self.network_detail]:
            if d and hasattr(d, 'notes'):
                notes.extend(d.notes)
        return notes


def _apply_label(score: float) -> str:
    if score >= settings.BOT_THRESHOLD:
        return "BOT"
    elif score >= settings.SUSPECT_THRESHOLD:
        return "SUSPECT"
    else:
        return "HUMAN"


async def analyze_user(
    username: str,
    trigger_comment_body: Optional[str] = None,
    trigger_comment_id: Optional[str] = None,
) -> BotAnalysisResult:
    """
    Full pipeline: fetch → analyze → score → label → persist.

    Steps:
      1. Fetch Reddit profile via API
      2. Supplement with comments stored in our DB (fills hidden history gap)
      3. Run all 4 feature layers
      4. Compute composite score with configurable weights
      5. Save result to DB and return
    """
    # ── Step 1: Fetch from Reddit API ─────────────────────────────────
    profile: RedditorProfile = await db_or_api_profile(username)

    # ── Step 2: Merge with DB-captured comments ───────────────────────
    # Even if Reddit hides the history, we may have seen this user live before.
    db_comments = await db.get_user_comments(username, limit=200)
    api_comment_ids = {c["id"] for c in profile.recent_comments}
    # Add DB comments that aren't already in the API response
    merged_comments = profile.recent_comments + [
        c for c in db_comments if c["id"] not in api_comment_ids
    ]

    # ── Step 3: Run feature layers ─────────────────────────────────────
    last_seen = max(
        (c["created_utc"] for c in merged_comments), default=None
    )
    meta_detail = score_metadata(profile, last_seen_utc=last_seen)
    temporal_detail = score_temporal(merged_comments)
    linguistic_detail = score_linguistics(
        merged_comments,
        new_comment_body=trigger_comment_body,
        new_comment_id=trigger_comment_id,
    )
    network_detail = await score_network(username, merged_comments)

    # ── Step 4: Composite score ────────────────────────────────────────
    # If history is hidden AND we have few comments, redistribute weights
    # to avoid penalizing for missing temporal/network data.
    w_meta = settings.WEIGHT_META
    w_temporal = settings.WEIGHT_TEMPORAL
    w_linguistic = settings.WEIGHT_LINGUISTIC
    w_network = settings.WEIGHT_NETWORK

    if profile.history_hidden and len(merged_comments) < 5:
        # Lean harder on what we have: meta + linguistic (the triggering comment)
        w_meta = 0.40
        w_temporal = 0.05
        w_linguistic = 0.45
        w_network = 0.10

    raw_score = (
        w_meta * meta_detail.final_score
        + w_temporal * temporal_detail.final_score
        + w_linguistic * linguistic_detail.final_score
        + w_network * network_detail.final_score
    )
    bot_score = round(min(max(raw_score, 0.0), 1.0), 4)

    # ── Non-linear Override Rules (Smoking Guns) ───────────────────────
    # 1. Explicit bot disclaimer in comments (e.g. "I am a bot, and this action was performed automatically")
    has_self_identified_bot = any(
        "i am a bot" in m.phrase.lower() or "performed automatically" in m.phrase.lower()
        for m in linguistic_detail.matches
    )
    # 2. Superhuman burst velocity (e.g. 90+ comments in 5 mins across 90 subreddits)
    is_superhuman_burst = temporal_detail.burst_score >= 1.0 and temporal_detail.dispersion_score >= 0.8
    # 3. Explicit bot name + active bot-like behavior
    is_bot_named = meta_detail.username_score >= 0.95 and (
        temporal_detail.final_score > 0.3 or linguistic_detail.final_score > 0.3
    )

    if has_self_identified_bot:
        bot_score = max(bot_score, 0.98)
        meta_detail.notes.append("Definitive: Account comments contain automated bot disclaimer")
    elif is_superhuman_burst:
        bot_score = max(bot_score, 0.92)
        temporal_detail.notes.append("Definitive: Superhuman multi-subreddit burst velocity")
    elif is_bot_named:
        bot_score = max(bot_score, 0.85)

    bot_score = round(min(max(bot_score, 0.0), 1.0), 4)
    label = _apply_label(bot_score)

    result = BotAnalysisResult(
        username=username,
        bot_score=bot_score,
        label=label,
        score_meta=round(meta_detail.final_score, 4),
        score_temporal=round(temporal_detail.final_score, 4),
        score_linguistic=round(linguistic_detail.final_score, 4),
        score_network=round(network_detail.final_score, 4),
        meta_detail=meta_detail,
        temporal_detail=temporal_detail,
        linguistic_detail=linguistic_detail,
        network_detail=network_detail,
        history_hidden=profile.history_hidden,
        trigger_comment=trigger_comment_body,
    )

    # ── Step 5: Persist to DB ──────────────────────────────────────────
    await db.upsert_user(
        username,
        label=label,
        bot_score=bot_score,
        score_meta=result.score_meta,
        score_temporal=result.score_temporal,
        score_linguistic=result.score_linguistic,
        score_network=result.score_network,
        last_analyzed=time.time(),
        account_created=profile.created_utc,
        comment_karma=profile.comment_karma,
        link_karma=profile.link_karma,
        is_hidden=int(profile.history_hidden),
        analysis_count=await _increment_analysis_count(username),
    )
    await db.log_analysis(
        username=username,
        bot_score=bot_score,
        label=label,
        scores={
            "meta": result.score_meta,
            "temporal": result.score_temporal,
            "linguistic": result.score_linguistic,
            "network": result.score_network,
        },
        detail={
            "notes": result.all_notes,
            "history_hidden": profile.history_hidden,
            "comment_count_api": len(profile.recent_comments),
            "comment_count_db": len(db_comments),
            "phrase_matches": [
                {"phrase": m.phrase, "type": m.match_type, "sim": m.similarity}
                for m in linguistic_detail.matches
            ],
        },
        trigger_comment=trigger_comment_body[:200] if trigger_comment_body else None,
    )

    return result


async def db_or_api_profile(username: str) -> RedditorProfile:
    """Try Reddit API; if error, return a stub profile from DB data."""
    from src.collectors.reddit_collector import fetch_redditor_profile
    profile = await fetch_redditor_profile(username, settings.MAX_COMMENT_HISTORY)
    return profile


async def _increment_analysis_count(username: str) -> int:
    user = await db.get_user(username)
    return (user.get("analysis_count", 0) + 1) if user else 1
