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
    evidence_tier: str = "TIER_3_BEHAVIORAL_FORENSICS"  # TIER_1_DETERMINISTIC / TIER_2_COMMUNITY_WATCHLIST / TIER_3_BEHAVIORAL_FORENSICS
    meta_detail: Optional[MetaScoreDetail] = None
    temporal_detail: Optional[TemporalScoreDetail] = None
    linguistic_detail: Optional[LinguisticScoreDetail] = None
    network_detail: Optional[NetworkScoreDetail] = None
    history_hidden: bool = False
    trigger_comment: Optional[str] = None
    custom_notes: list[str] = field(default_factory=list)
    analyzed_at: float = field(default_factory=time.time)

    @property
    def all_notes(self) -> list[str]:
        notes = list(self.custom_notes)
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
    bypass_watchlist: bool = False,
) -> BotAnalysisResult:
    """
    Full 3-Tier Evidence Pipeline:
      Tier 1: Deterministic Ground Truth (Disclaimers, superhuman burst)
      Tier 2: Community Consensus & Watchlists (Instant O(1) resolution)
      Tier 3: Multi-Layer Behavioral Forensics (Meta, Temporal, Linguistic, Network)
    """
    clean_username = username.replace("u/", "").strip()

    # ── Tier 2: Check Community Watchlist Registry (O(1) Instant Resolution) ──
    if not bypass_watchlist:
        wl_entry = await db.get_watchlist_entry(clean_username)
        if wl_entry:
            wl_label = wl_entry["list_type"].upper()
            wl_score = float(wl_entry["score"])
            meta_s = 0.20 if wl_label == "BOT" else 0.04
            temp_s = 0.85 if wl_label == "BOT" else 0.10
            ling_s = 0.90 if wl_label == "BOT" else 0.04
            result = BotAnalysisResult(
                username=clean_username,
                bot_score=wl_score,
                label=wl_label,
                evidence_tier="TIER_2_COMMUNITY_WATCHLIST",
                score_meta=meta_s,
                score_temporal=temp_s,
                score_linguistic=ling_s,
                score_network=0.0,
                meta_detail=MetaScoreDetail(final_score=meta_s),
                temporal_detail=TemporalScoreDetail(final_score=temp_s),
                linguistic_detail=LinguisticScoreDetail(final_score=ling_s),
                network_detail=NetworkScoreDetail(final_score=0.0),
                history_hidden=False,
                trigger_comment=trigger_comment_body,
                custom_notes=[
                    f"Tier 2: Community verified watchlist ({wl_entry.get('category', 'registry')})",
                    wl_entry.get("notes") or ("Confirmed automated bot" if wl_label == "BOT" else "Confirmed human account")
                ],
            )
            # Store visible comment if provided
            if trigger_comment_body:
                await db.save_comment(
                    comment_id=trigger_comment_id or f"live_{int(time.time()*1000)}",
                    username=clean_username,
                    subreddit="current_page",
                    body=trigger_comment_body,
                    created_utc=time.time(),
                    score=1,
                    is_bot_phrase=(wl_label == "BOT"),
                )
            await db.upsert_user(
                clean_username,
                label=wl_label,
                bot_score=wl_score,
                score_meta=result.score_meta,
                score_temporal=result.score_temporal,
                score_linguistic=result.score_linguistic,
                score_network=result.score_network,
                last_analyzed=time.time(),
                is_hidden=0,
            )
            return result

    # ── Tier 1 Fast-Path: On-Page Visible Comment Bot Disclaimer Check ─────────
    if trigger_comment_body:
        norm_trigger = trigger_comment_body.lower()
        if "i am a bot" in norm_trigger or "performed automatically" in norm_trigger:
            result = BotAnalysisResult(
                username=clean_username,
                bot_score=0.98,
                label="BOT",
                evidence_tier="TIER_1_DETERMINISTIC",
                score_meta=0.25,
                score_temporal=0.85,
                score_linguistic=1.0,
                score_network=0.0,
                meta_detail=MetaScoreDetail(final_score=0.25),
                temporal_detail=TemporalScoreDetail(final_score=0.85),
                linguistic_detail=LinguisticScoreDetail(final_score=1.0, phrase_score=1.0),
                network_detail=NetworkScoreDetail(final_score=0.0),
                history_hidden=False,
                trigger_comment=trigger_comment_body,
                custom_notes=["Tier 1: Definitive automated bot disclaimer detected in on-page comment"],
            )
            await db.save_comment(
                comment_id=trigger_comment_id or f"live_{int(time.time()*1000)}",
                username=clean_username,
                subreddit="current_page",
                body=trigger_comment_body,
                created_utc=time.time(),
                score=1,
                is_bot_phrase=True,
            )
            await db.upsert_user(clean_username, label="BOT", bot_score=0.98, is_hidden=0)
            return result

    # ── Step 1: Fetch Profile from API / Public Archives ──────────────────────
    profile: RedditorProfile = await db_or_api_profile(clean_username)

    # ── Step 2: Merge with DB Comments + Current On-Page Visible Comment ──────
    db_comments = await db.get_user_comments(clean_username, limit=200)
    api_comment_ids = {c["id"] for c in profile.recent_comments}
    merged_comments = profile.recent_comments + [
        c for c in db_comments if c["id"] not in api_comment_ids
    ]

    # Crucial: If current page has a visible comment, ensure it's in merged_comments
    if trigger_comment_body:
        cid = trigger_comment_id or f"onpage_{int(time.time()*1000)}"
        if cid not in {c.get("id") for c in merged_comments}:
            merged_comments.insert(0, {
                "id": cid,
                "body": trigger_comment_body,
                "subreddit": "current_page",
                "created_utc": time.time(),
                "score": 1,
                "permalink": "",
                "submission_id": "",
                "parent_id": "",
            })
            await db.save_comment(
                comment_id=cid,
                username=clean_username,
                subreddit="current_page",
                body=trigger_comment_body,
                created_utc=time.time(),
                score=1,
            )

    # ── Step 3: Run Tier 3 Feature Layers ─────────────────────────────────────
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
    network_detail = await score_network(clean_username, merged_comments)

    # ── Step 4: Dynamic Weight Redistribution for Sparse / Hidden Profiles ───
    w_meta = settings.WEIGHT_META
    w_temporal = settings.WEIGHT_TEMPORAL
    w_linguistic = settings.WEIGHT_LINGUISTIC
    w_network = settings.WEIGHT_NETWORK

    has_network_data = (
        network_detail.cooccurrence_score > 0
        or network_detail.reply_pair_score > 0
        or network_detail.known_bot_association > 0
    )

    if profile.history_hidden and len(merged_comments) < 5:
        # History private: heavily prioritize on-page observable comment text + metadata
        w_meta = 0.40
        w_temporal = 0.05
        w_linguistic = 0.45
        w_network = 0.10
    elif not has_network_data:
        active_sum = w_meta + w_temporal + w_linguistic
        w_meta = w_meta / active_sum
        w_temporal = w_temporal / active_sum
        w_linguistic = w_linguistic / active_sum
        w_network = 0.0

    raw_score = (
        w_meta * meta_detail.final_score
        + w_temporal * temporal_detail.final_score
        + w_linguistic * linguistic_detail.final_score
        + w_network * network_detail.final_score
    )
    bot_score = raw_score

    # ── Tier 1 Deterministic Verification & Bayesian Boosts ───────────────────
    evidence_tier = "TIER_3_BEHAVIORAL_FORENSICS"

    has_self_identified_bot = any(
        "i am a bot" in m.phrase.lower() or "performed automatically" in m.phrase.lower()
        for m in linguistic_detail.matches
    )
    is_superhuman_burst = (
        temporal_detail.burst_score >= 1.0 and temporal_detail.dispersion_score >= 0.8
    )
    is_template_bot = linguistic_detail.template_score >= 0.85
    is_bot_named = meta_detail.username_score >= 0.95 and (
        temporal_detail.final_score > 0.25 or linguistic_detail.final_score > 0.25
    )

    boost_factor = 0.0
    if has_self_identified_bot:
        boost_factor = max(boost_factor, 0.85)
        evidence_tier = "TIER_1_DETERMINISTIC"
        meta_detail.notes.append("Tier 1: Definitive automated bot disclaimer in comments")
    if is_superhuman_burst:
        boost_factor = max(boost_factor, 0.80)
        evidence_tier = "TIER_1_DETERMINISTIC"
        temporal_detail.notes.append("Tier 1: Definitive superhuman multi-subreddit burst velocity")
    if is_template_bot:
        boost_factor = max(boost_factor, 0.70)
        linguistic_detail.notes.append("Definitive: Programmatic comment template repetition")
    if is_bot_named:
        boost_factor = max(boost_factor, 0.55)

    if boost_factor > 0:
        bot_score = raw_score + (1.0 - raw_score) * boost_factor

    bot_score = round(min(max(bot_score, 0.0), 1.0), 4)
    label = _apply_label(bot_score)

    result = BotAnalysisResult(
        username=clean_username,
        bot_score=bot_score,
        label=label,
        evidence_tier=evidence_tier,
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
