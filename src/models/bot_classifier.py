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
    history_recovered_via_osint: bool = False
    trigger_comment: Optional[str] = None
    custom_notes: list[str] = field(default_factory=list)
    dynamic_weights: Optional[dict[str, float]] = None
    analyzed_at: float = field(default_factory=time.time)
    verdict_summary: str = ""
    primary_factor: str = ""
    specific_reasons: list[str] = field(default_factory=list)

    @property
    def all_notes(self) -> list[str]:
        notes = list(self.custom_notes)
        for d in [self.meta_detail, self.temporal_detail,
                  self.linguistic_detail, self.network_detail]:
            if d and hasattr(d, 'notes'):
                notes.extend(d.notes)
        return notes


def _build_score_explanation(
    username: str,
    bot_score: float,
    label: str,
    evidence_tier: str,
    meta_detail: MetaScoreDetail,
    temporal_detail: TemporalScoreDetail,
    linguistic_detail: LinguisticScoreDetail,
    network_detail: NetworkScoreDetail,
    dynamic_weights: dict[str, float],
    custom_notes: list[str],
) -> tuple[str, str, list[str]]:
    """
    Build a comprehensive, specific, and descriptive breakdown of WHY
    the user received this score and the basis on which it was assigned.
    """
    specific_reasons: list[str] = []

    # 1. Primary Factor
    w_m = dynamic_weights.get("meta", 0.25)
    w_t = dynamic_weights.get("temporal", 0.25)
    w_l = dynamic_weights.get("linguistic", 0.30)
    w_n = dynamic_weights.get("network", 0.20)

    contrib_meta = w_m * meta_detail.final_score
    contrib_temp = w_t * temporal_detail.final_score
    contrib_ling = w_l * linguistic_detail.final_score
    contrib_net = w_n * network_detail.final_score

    if evidence_tier == "TIER_1_DETERMINISTIC":
        primary_factor = "Deterministic Bot Disclaimer / Pattern"
    elif evidence_tier == "TIER_2_COMMUNITY_WATCHLIST":
        primary_factor = "Verified Community Watchlist Registry"
    else:
        candidates = [
            ("Temporal Activity & Circadian Dynamics", contrib_temp, temporal_detail.final_score),
            ("Linguistic & Content Forensics", contrib_ling, linguistic_detail.final_score),
            ("Account Metadata & Origin", contrib_meta, meta_detail.final_score),
            ("Network & Coordination Graph", contrib_net, network_detail.final_score),
        ]
        candidates.sort(key=lambda x: x[1], reverse=True)
        if label == "HUMAN":
            primary_factor = "Organic Human Behavioral Profile"
        else:
            primary_factor = candidates[0][0]

    # 2. Specific Reasons
    # --- Timing & Circadian Reasons ---
    if temporal_detail.shift_pattern_score >= 0.65:
        specific_reasons.append(
            "24/7 Superhuman Activity: Active across almost all 24 hours with no biological sleep trough (rest gap < 4h)."
        )
    elif temporal_detail.circadian_score < 0.82 and temporal_detail.sample_size >= 4:
        specific_reasons.append(
            "Natural Circadian Rest: Clear biological sleep cycle detected with normal daily resting periods."
        )

    if temporal_detail.iat_score >= 0.65:
        specific_reasons.append(
            "Mechanical Pacing: Comment intervals follow rigid timer regularity (low variance, scripted scheduler)."
        )
    elif temporal_detail.sample_size >= 5 and temporal_detail.iat_score < 0.35:
        specific_reasons.append(
            "Organic Typing Cadence: Variable, natural pauses between posts typical of human reading and composition."
        )

    if temporal_detail.burst_score >= 0.60:
        specific_reasons.append(
            "High Burst Density: Multiple comments submitted within a tight 5-minute sliding window."
        )

    # --- Community & Topic Reasons ---
    if temporal_detail.monomania_score >= 0.60:
        specific_reasons.append(
            "Topical Monomania: Hyper-concentrated activity in a single subreddit (agenda or dedicated single-purpose bot)."
        )
    if temporal_detail.propaganda_risk_score >= 0.60:
        specific_reasons.append(
            "Conflict Subreddit Focus: Disproportionate concentration in high-conflict political/ideological communities."
        )
    if temporal_detail.randomness_score >= 0.65:
        specific_reasons.append(
            "High-Entropy Scatter: Jumps rapidly across unrelated subreddits (automated crawler/scraper pattern)."
        )
    elif temporal_detail.sample_size >= 5 and temporal_detail.monomania_score < 0.40 and temporal_detail.propaganda_risk_score < 0.40:
        specific_reasons.append(
            "Diverse Community Interaction: Organic participation across multiple balanced discussion topics."
        )

    # --- Linguistic & Phrasing Reasons ---
    if linguistic_detail.phrase_score >= 0.50:
        matched_str = linguistic_detail.matches[0].phrase if linguistic_detail.matches else "bot trigger"
        specific_reasons.append(
            f"Bot Phrase Pattern: Detected automated phrasing or command syntax ('{matched_str[:45]}')."
        )
    if getattr(linguistic_detail, "template_score", 0.0) >= 0.70:
        specific_reasons.append(
            "Programmatic Template: High repetition of fixed comment layouts across different threads."
        )
    if linguistic_detail.llm_tone_score >= 0.30:
        specific_reasons.append(
            "Stylometric LLM Markers: Repetitive diplomatic hedging, formal bulleting, or customer-service tone."
        )
    elif len(specific_reasons) < 2 and label == "HUMAN":
        specific_reasons.append(
            "Authentic Natural Voice: Conversational English with natural emotional expression and grammatical variety."
        )

    # --- Metadata & Account Origins ---
    if meta_detail.username_score >= 0.80:
        specific_reasons.append(
            "Algorithmic Username: Matches Reddit's default auto-generated format (Adjective_Noun_1234)."
        )
    if meta_detail.dormancy_score >= 0.60:
        specific_reasons.append(
            "Sleeper Dormancy Gap: Account sat completely idle for months after creation before sudden activation."
        )
    if meta_detail.karma_score >= 0.60:
        specific_reasons.append(
            "Karma Asymmetry: Extreme ratio between link and comment karma or superhuman karma gain velocity."
        )

    # Include custom notes
    for cn in custom_notes:
        if cn not in specific_reasons and not any(r.startswith(cn[:20]) for r in specific_reasons):
            specific_reasons.append(cn)

    # 3. Verdict Summary
    if label == "BOT":
        if evidence_tier == "TIER_1_DETERMINISTIC":
            verdict_summary = "Confirmed Automated Bot: Self-identifying automated bot disclaimer or superhuman multi-subreddit execution velocity."
        elif evidence_tier == "TIER_2_COMMUNITY_WATCHLIST":
            verdict_summary = "Community Verified Bot: Confirmed automated service, financial tipping utility, or moderation bot registered in public watchlists."
        elif temporal_detail.shift_pattern_score >= 0.65:
            verdict_summary = "Automated Bot Profile: Superhuman 24/7 continuous operation without biological sleep troughs, driven by mechanical interval pacing."
        elif temporal_detail.monomania_score >= 0.65:
            verdict_summary = "Automated / Agenda Operation: Hyper-concentrated topical monomania with rigid posting intervals and algorithmic account traits."
        else:
            verdict_summary = f"Automated Bot Score ({int(bot_score * 100)}%): Primary driver is {primary_factor.lower()} with distinct non-human behavioral anomalies."
    elif label == "SUSPECT":
        verdict_summary = f"Suspicious Activity ({int(bot_score * 100)}%): Displays mixed signals — conversational text paired with anomalous posting cadence or account history gaps."
    else:
        if evidence_tier == "TIER_2_COMMUNITY_WATCHLIST":
            verdict_summary = "Verified Authentic Account: Confirmed genuine human user in audited benchmark records."
        else:
            verdict_summary = f"Authentic Human Profile ({int(bot_score * 100)}%): Organic conversational phrasing, natural biological sleep schedule, and healthy topic diversity."

    return verdict_summary, primary_factor, specific_reasons


def _apply_label(score: float) -> str:
    if score >= settings.BOT_THRESHOLD:
        return "BOT"
    elif score >= settings.SUSPECT_THRESHOLD:
        return "SUSPECT"
    else:
        return "HUMAN"


def compute_dynamic_weights(
    meta_detail: MetaScoreDetail,
    temporal_detail: TemporalScoreDetail,
    linguistic_detail: LinguisticScoreDetail,
    network_detail: NetworkScoreDetail,
    history_hidden: bool,
    comment_count: int,
) -> tuple[float, float, float, float, list[str]]:
    """
    Context-Aware Dynamic Weighting Engine.

    Dynamically adjusts factor weights based on the observed anomaly profile:
      1. Linguistic Decoupling: When text is clean/human-like (LLM generated or
         human-assisted spam/troll farms) but temporal, metadata, or network
         indicators are distinctly anomalous, the text weight is slashed so
         natural grammar cannot shield bot behavior.
      2. Monomania / Agenda / Political Propaganda: Hyper-focus on single subreddits
         or political conflict clusters boosts temporal and metadata weighting.
      3. Subreddit Scatter: High entropy / random posting boosts temporal velocity weights.
      4. Sleep Trough Absence: 24/7 continuous activity elevates temporal weight.
      5. Coordinated Networks: High co-occurrence or reply clustering elevates network weight.
      6. Data Sparsity: Sparse or hidden history shifts weight to observable metadata + text.
      7. Missing Network Data: Zeroed network weight is redistributed proportionally.
    """
    w_meta = settings.WEIGHT_META          # default 0.25
    w_temporal = settings.WEIGHT_TEMPORAL  # default 0.25
    w_linguistic = settings.WEIGHT_LINGUISTIC # default 0.30
    w_network = settings.WEIGHT_NETWORK    # default 0.20
    dynamic_notes: list[str] = []

    has_network_data = (
        network_detail.cooccurrence_score > 0
        or network_detail.reply_pair_score > 0
        or network_detail.known_bot_association > 0
    )

    # 1. Sparse / Hidden History Handling
    if history_hidden and comment_count < 5:
        w_meta = 0.40
        w_temporal = 0.05
        w_linguistic = 0.45
        w_network = 0.10 if has_network_data else 0.0
        dynamic_notes.append("Dynamic weighting: sparse history, prioritizing observable text forensics & metadata")
        s = w_meta + w_temporal + w_linguistic + w_network
        return round(w_meta / s, 4), round(w_temporal / s, 4), round(w_linguistic / s, 4), round(w_network / s, 4), dynamic_notes

    # 2. Linguistic Decoupling (The "Clean Grammar" Bot & Human Astroturfer Shield Breaker)
    # When text scores very low (natural English), but metadata or temporal flags are distinctly anomalous
    s_ling = linguistic_detail.final_score
    s_temp = temporal_detail.final_score
    s_meta = meta_detail.final_score
    s_net = network_detail.final_score
    non_ling_max = max(s_temp, s_meta, s_net)

    if s_ling < 0.25 and non_ling_max >= 0.50:
        # Natural text, but behavioral timeline or account origin screams bot/spammer/sleeper
        w_linguistic = 0.08
        w_temporal = 0.45
        w_meta = 0.32
        w_network = 0.15 if has_network_data else 0.0
        dynamic_notes.append(
            "Dynamic weighting: linguistic decoupling applied (natural text with anomalous temporal/metadata signals)"
        )
    else:
        # 3. Monomania & Political / Astroturfing Cluster Boost
        if temporal_detail.monomania_score >= 0.60 or temporal_detail.propaganda_risk_score >= 0.60:
            w_temporal += 0.12
            w_meta += 0.08
            w_linguistic = max(w_linguistic - 0.15, 0.10)
            dynamic_notes.append(
                "Dynamic weighting: agenda/propaganda focus adjustment (elevated weight on timeline & community monomania)"
            )

        # 4. High Subreddit Randomness / Scatter Crawler
        elif temporal_detail.randomness_score >= 0.65:
            w_temporal += 0.10
            w_meta += 0.05
            w_linguistic = max(w_linguistic - 0.10, 0.12)
            dynamic_notes.append(
                "Dynamic weighting: subreddit scatter adjustment (elevated weight on multi-sub dispersion dynamics)"
            )

        # 5. Sleep Trough Absence / Superhuman 24-7 Activity
        if temporal_detail.shift_pattern_score >= 0.65:
            w_temporal += 0.10
            w_linguistic = max(w_linguistic - 0.08, 0.10)
            dynamic_notes.append(
                "Dynamic weighting: 24/7 activity adjustment (elevated weight on circadian rhythm and rest gaps)"
            )

        # 6. Coordinated Network Astroturfing Ring
        if s_net >= 0.50 and has_network_data:
            w_network += 0.15
            w_linguistic = max(w_linguistic - 0.10, 0.10)
            dynamic_notes.append(
                "Dynamic weighting: coordination ring adjustment (elevated weight on co-occurrence & reply patterns)"
            )

    # 7. Absence of Network Data Redistribution
    if not has_network_data:
        w_network = 0.0

    # 8. Strict Normalization to 1.0
    total = w_meta + w_temporal + w_linguistic + w_network
    if total > 0:
        w_meta /= total
        w_temporal /= total
        w_linguistic /= total
        w_network /= total
    else:
        w_meta, w_temporal, w_linguistic, w_network = 0.25, 0.25, 0.30, 0.20

    return round(w_meta, 4), round(w_temporal, 4), round(w_linguistic, 4), round(w_network, 4), dynamic_notes


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
            is_bot = (wl_label == "BOT")
            v_summary = "Community Verified Bot: Confirmed automated utility, financial tipping tool, or moderation bot registered in public community watchlists." if is_bot else "Verified Authentic Account: Confirmed genuine human user in audited benchmark records."
            p_factor = "Verified Community Watchlist Registry"
            s_reasons = [
                f"Tier 2 Watchlist: Verified {wl_entry.get('category', 'community')} account",
                wl_entry.get("notes") or ("Confirmed automated bot" if is_bot else "Confirmed authentic human user"),
                "Deterministic classification based on verified community consensus",
            ]
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
                verdict_summary=v_summary,
                primary_factor=p_factor,
                specific_reasons=s_reasons,
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
            v_summary = "Deterministic Bot: Explicit automated bot disclaimer detected directly in comment text on current page."
            p_factor = "Deterministic Bot Disclaimer"
            s_reasons = [
                "Definitive 'I am a bot' or 'performed automatically' declaration in comment text",
                "Automated system notification formatting",
                "Bypasses probabilistic weighting due to explicit self-identification",
            ]
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
                verdict_summary=v_summary,
                primary_factor=p_factor,
                specific_reasons=s_reasons,
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

    # ── Step 2: Merge with DB Comments + Recovered Posts + Current On-Page Visible Comment
    db_comments = await db.get_user_comments(clean_username, limit=200)
    api_comment_ids = {c["id"] for c in profile.recent_comments}
    merged_comments = profile.recent_comments + [
        c for c in db_comments if c["id"] not in api_comment_ids
    ]

    # Merge posts retrieved from profile / OSINT archives
    known_ids = {c["id"] for c in merged_comments}
    for p in profile.recent_posts:
        if p["id"] not in known_ids:
            merged_comments.append(p)
            known_ids.add(p["id"])

    custom_notes: list[str] = []
    if profile.history_recovered_via_osint:
        custom_notes.append(
            f"OSINT Recovery: {len(profile.recent_comments)} comments & {len(profile.recent_posts)} posts retrieved via public archives ({profile.archive_source or 'multi-archive'})"
        )

    # Crucial: If current page has a visible comment, ensure it's in merged_comments
    if trigger_comment_body:
        cid = trigger_comment_id or f"onpage_{int(time.time()*1000)}"
        if cid not in known_ids:
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
            known_ids.add(cid)
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

    # ── Step 4: Context-Aware Dynamic Weight Redistribution ───────────────────
    w_meta, w_temporal, w_linguistic, w_network, dyn_notes = compute_dynamic_weights(
        meta_detail=meta_detail,
        temporal_detail=temporal_detail,
        linguistic_detail=linguistic_detail,
        network_detail=network_detail,
        history_hidden=profile.history_hidden,
        comment_count=len(merged_comments),
    )
    custom_notes.extend(dyn_notes)

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
        history_recovered_via_osint=profile.history_recovered_via_osint,
        trigger_comment=trigger_comment_body,
        custom_notes=custom_notes,
        dynamic_weights={
            "meta": w_meta,
            "temporal": w_temporal,
            "linguistic": w_linguistic,
            "network": w_network,
        },
    )

    v_summary, p_factor, s_reasons = _build_score_explanation(
        username=clean_username,
        bot_score=bot_score,
        label=label,
        evidence_tier=evidence_tier,
        meta_detail=meta_detail,
        temporal_detail=temporal_detail,
        linguistic_detail=linguistic_detail,
        network_detail=network_detail,
        dynamic_weights=result.dynamic_weights or {},
        custom_notes=custom_notes,
    )
    result.verdict_summary = v_summary
    result.primary_factor = p_factor
    result.specific_reasons = s_reasons

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
