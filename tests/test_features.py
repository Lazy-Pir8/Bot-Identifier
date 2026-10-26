"""
tests/test_features.py — Unit tests for all feature layers.

Run with:
    python -m pytest tests/ -v
"""
import sys
import os
import time
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from src.features.metadata_extractor import score_metadata, MetaScoreDetail
from src.features.temporal_analyzer import score_temporal
from src.features.text_forensics import score_linguistics, score_single_comment
from src.collectors.reddit_collector import RedditorProfile


# ─── Metadata Tests ───────────────────────────────────────────────────────────

class TestMetadataExtractor:

    def _make_profile(self, **kwargs) -> RedditorProfile:
        defaults = dict(
            username="NormalUser",
            created_utc=time.time() - 86400 * 365,  # 1 year old
            comment_karma=500,
            link_karma=100,
            is_suspended=False,
            history_hidden=False,
            has_verified_email=True,
            recent_comments=[],
        )
        defaults.update(kwargs)
        return RedditorProfile(**defaults)

    def test_default_username_mild_score(self):
        profile = self._make_profile(username="Cautious_Ad_8492")
        detail = score_metadata(profile)
        assert detail.username_score == 0.20, "Default username should be a mild/weak prior (0.20)"
        assert detail.is_default_username is True

    def test_explicit_bot_username_high_score(self):
        profile = self._make_profile(username="AutoModerator")
        detail = score_metadata(profile)
        assert detail.username_score >= 0.90, "Explicit bot username should score high"

    def test_custom_username_zero_score(self):
        profile = self._make_profile(username="Lazy-Pir8")
        detail = score_metadata(profile)
        assert detail.username_score == 0.0, "Custom username should score 0.0"
        assert detail.is_custom_username is True

    def test_dormancy_sampling_truncation_guard(self):
        # Account is 2 years old, but collector only fetched 25 comments from the past 2 weeks
        now = time.time()
        recent = [{"created_utc": now - 14 * 86400 + i * 3600} for i in range(25)]
        profile = self._make_profile(
            created_utc=now - 730 * 86400,
            comment_karma=850,
            recent_comments=recent,
        )
        detail = score_metadata(profile, last_seen_utc=now)
        # Oldest comment being 14 days old must NOT trigger a 716-day sleeper penalty
        assert detail.dormancy_score == 0.0, "Truncated recent comment sample should not be flagged as sleeper"

    def test_dormancy_velocity_coupling(self):
        now = time.time()
        # Sparse old account waking up with low human frequency (1 comment over 10 days)
        sparse_human_comments = [{"created_utc": now - 10 * 86400}]
        profile_human = self._make_profile(
            created_utc=now - 200 * 86400,
            comment_karma=5,
            recent_comments=sparse_human_comments,
        )
        detail_human = score_metadata(profile_human, last_seen_utc=now)
        assert detail_human.dormancy_score <= 0.10, "Low-velocity post-dormancy should be recognized as human"

        # Sparse old account waking up with high-velocity burst (15 comments in 1 day)
        burst_bot_comments = [{"created_utc": now - i * 3600} for i in range(15)]
        profile_bot = self._make_profile(
            created_utc=now - 200 * 86400,
            comment_karma=15,
            recent_comments=burst_bot_comments,
        )
        detail_bot = score_metadata(profile_bot, last_seen_utc=now)
        assert detail_bot.dormancy_score >= 0.35, "High-velocity sudden activation should be flagged"

    def test_normal_username_low_score(self):
        profile = self._make_profile(username="xkcd_fan_42")
        detail = score_metadata(profile)
        assert detail.username_score < 0.5, "Normal username should score low"

    def test_suspended_account_max_flag(self):
        profile = self._make_profile(is_suspended=True)
        detail = score_metadata(profile)
        assert detail.flag_score == 1.0, "Suspended account should get max flag score"

    def test_new_account_higher_score(self):
        profile_new = self._make_profile(created_utc=time.time() - 86400 * 2)  # 2 days
        profile_old = self._make_profile(created_utc=time.time() - 86400 * 365)  # 1 year
        new_score = score_metadata(profile_new)
        old_score = score_metadata(profile_old)
        assert new_score.age_score > old_score.age_score

    def test_karma_asymmetry_link_heavy(self):
        profile = self._make_profile(link_karma=50000, comment_karma=50)
        detail = score_metadata(profile)
        assert detail.karma_score >= 0.7, "Extreme link karma dominance should score high"

    def test_clean_profile_low_score(self):
        profile = self._make_profile(
            username="regular_redditor",
            created_utc=time.time() - 86400 * 730,
            comment_karma=3000,
            link_karma=500,
            has_verified_email=True,
        )
        detail = score_metadata(profile)
        assert detail.final_score < 0.3, "Clean profile should score low"


# ─── Temporal Tests ────────────────────────────────────────────────────────────

class TestTemporalAnalyzer:

    def _make_comments_uniform(self, count=48) -> list[dict]:
        """Simulate a bot posting uniformly across all 24 hours (2 per hour)."""
        base = 1_700_000_000.0  # fixed reference timestamp (doesn't matter which day)
        comments = []
        for i in range(count):
            # Each comment lands in a different hour slot, cycling through 0-23
            hour = i % 24
            ts = base + hour * 3600 + (i // 24) * 86400
            comments.append({"created_utc": ts, "subreddit": "AskReddit"})
        return comments

    def _make_comments_human(self, count=30) -> list[dict]:
        """Simulate a human posting only 8am-10pm, randomly."""
        import random
        import datetime
        comments = []
        base = time.time()
        for i in range(count):
            # Pick random hour in 8-22 range for last 30 days
            day_offset = random.randint(0, 29) * 86400
            hour_offset = random.randint(8, 22) * 3600
            minute_offset = random.randint(0, 59) * 60
            ts = base - day_offset - 86400 + hour_offset + minute_offset
            comments.append({"created_utc": ts, "subreddit": "learnpython"})
        return sorted(comments, key=lambda c: c["created_utc"])

    def test_uniform_posting_high_circadian(self):
        comments = self._make_comments_uniform(48)
        detail = score_temporal(comments)
        assert detail.circadian_score > 0.8, "24/7 uniform posting should score high on circadian"

    def test_regular_interval_high_iat(self):
        # Very regular: every exactly 300 seconds
        now = time.time()
        comments = [
            {"created_utc": now - i * 300, "subreddit": "test"}
            for i in range(30)
        ]
        detail = score_temporal(comments)
        assert detail.iat_score >= 0.60, "Extremely regular interval should score high on IAT"

    def test_too_few_comments_returns_zero(self):
        comments = [{"created_utc": time.time(), "subreddit": "test"}]
        detail = score_temporal(comments)
        assert detail.final_score == 0.0

    def test_burst_detection(self):
        now = time.time()
        # 20 comments in a 3-minute window
        burst = [{"created_utc": now - i * 9, "subreddit": "AskReddit"} for i in range(20)]
        detail = score_temporal(burst)
        assert detail.burst_score >= 0.6, "Burst posting should be detected"


# ─── Linguistic Tests ──────────────────────────────────────────────────────────

class TestTextForensics:

    def test_exact_phrase_match(self):
        body = "This! 100% this. Underrated comment right here."
        score, matches = score_single_comment(body)
        assert score > 0.5, f"Should match bot phrase, got {score}"
        assert len(matches) > 0

    def test_llm_marker_detection(self):
        body = (
            "That's a great question! "
            "It's important to remember that all perspectives have merit. "
            "Hope this helps! Feel free to ask if you need more."
        )
        score, matches = score_single_comment(body)
        # Should get LLM marker match
        assert any(m.match_type == "llm_marker" for m in matches)

    def test_normal_comment_low_score(self):
        body = "Honestly I disagree, I've been playing guitar for 10 years and it was never this bad lol"
        score, matches = score_single_comment(body)
        assert score < 0.5, f"Normal comment should score low, got {score}"

    def test_scam_pattern_match(self):
        body = "DM me for investment opportunities, I doubled my money last week!"
        score, matches = score_single_comment(body)
        assert score > 0.5, "Scam pattern should be detected"

    def test_multiple_comments_scoring(self):
        comments = [
            {"body": "This! 100% this.", "id": "c1"},
            {"body": "Great question! Hope this helps!", "id": "c2"},
            {"body": "Communication is key in any healthy relationship.", "id": "c3"},
        ]
        detail = score_linguistics(comments)
        assert detail.final_score > 0.4, "Multiple bot comments should score high"

    def test_empty_comments_returns_zero(self):
        detail = score_linguistics([])
        assert detail.final_score == 0.0

    def test_template_self_similarity_detection(self):
        # Repetitive template comments (like tippr or downloader bots)
        comments = [
            {"body": "User A, you've received 0.001 BCH! Click here for details.", "id": "t1"},
            {"body": "User B, you've received 0.005 BCH! Click here for details.", "id": "t2"},
            {"body": "User C, you've received 0.002 BCH! Click here for details.", "id": "t3"},
        ]
        detail = score_linguistics(comments)
        assert detail.template_score >= 0.70, f"Template score should be high, got {detail.template_score}"
        assert detail.final_score >= 0.70, f"Final linguistic score should be high, got {detail.final_score}"

    def test_subreddit_randomness_and_karma_farming(self):
        now = time.time()
        # High dispersion across 16 random unique subreddits including a karma farming sub
        comments = [
            {"created_utc": now - i * 60, "subreddit": f"random_sub_{i}", "body": "test"}
            for i in range(16)
        ]
        comments.append({"created_utc": now, "subreddit": "FreeKarma4U", "body": "Upvoted! Please return"})
        detail = score_temporal(comments)
        assert detail.randomness_score >= 0.6, f"Expected high randomness score, got {detail.randomness_score}"
        assert detail.karma_farm_score >= 0.9, f"Expected karma farm score >= 0.9, got {detail.karma_farm_score}"
        assert any("Karma-farming" in n for n in detail.notes)


    def test_monomania_and_political_propaganda_cluster(self):
        now = time.time()
        # 12 comments, 10 of which are in r/politics (83% concentration in a political sub)
        comments = [
            {"created_utc": now - i * 3600, "subreddit": "politics", "body": "Political debate statement"}
            for i in range(10)
        ]
        comments.append({"created_utc": now - 11 * 3600, "subreddit": "worldnews", "body": "News statement"})
        comments.append({"created_utc": now - 12 * 3600, "subreddit": "AskReddit", "body": "General question"})
        detail = score_temporal(comments)
        assert detail.monomania_score >= 0.70, f"Expected high monomania score, got {detail.monomania_score}"
        assert detail.propaganda_risk_score >= 0.80, f"Expected high propaganda risk score, got {detail.propaganda_risk_score}"
        assert any("Hyper-focused" in n or "Ideological" in n for n in detail.notes)

    def test_sleep_trough_absence_and_24_7_activity(self):
        now = time.time()
        # Comments spread across 22 distinct hours of the day
        comments = [
            {"created_utc": now - h * 3600, "subreddit": "AskReddit", "body": "test"}
            for h in range(22)
        ]
        detail = score_temporal(comments)
        assert detail.shift_pattern_score >= 0.65, f"Expected elevated shift pattern score, got {detail.shift_pattern_score}"
        assert any("24" in n for n in detail.notes)


# ─── Evidence Tier & Dynamic Weighting Tests ────────────────────────────────────

class TestDynamicWeighting:

    def test_linguistic_decoupling_for_clean_text_bot(self):
        from src.models.bot_classifier import compute_dynamic_weights
        from src.features.metadata_extractor import MetaScoreDetail
        from src.features.temporal_analyzer import TemporalScoreDetail
        from src.features.text_forensics import LinguisticScoreDetail
        from src.features.network_mapper import NetworkScoreDetail

        # Simulating an LLM or paid-human astroturf bot:
        # Perfectly natural text (linguistic score = 0.05)
        # But sleeper account (meta = 0.85) and 24/7 robotic velocity (temporal = 0.90)
        meta = MetaScoreDetail(final_score=0.85)
        temp = TemporalScoreDetail(final_score=0.90)
        ling = LinguisticScoreDetail(final_score=0.05)
        net = NetworkScoreDetail(final_score=0.0)

        w_meta, w_temp, w_ling, w_net, notes = compute_dynamic_weights(
            meta_detail=meta,
            temporal_detail=temp,
            linguistic_detail=ling,
            network_detail=net,
            history_hidden=False,
            comment_count=25,
        )

        assert w_ling <= 0.12, f"Clean text weight should be decoupled/lowered, got {w_ling}"
        assert w_temp >= 0.40, f"Temporal weight should dominate, got {w_temp}"
        assert w_meta >= 0.30, f"Meta weight should dominate, got {w_meta}"
        assert abs((w_meta + w_temp + w_ling + w_net) - 1.0) < 0.01, "Weights must sum to 1.0"
        assert any("linguistic decoupling" in n for n in notes)

    def test_monomania_and_agenda_weight_boost(self):
        from src.models.bot_classifier import compute_dynamic_weights
        from src.features.metadata_extractor import MetaScoreDetail
        from src.features.temporal_analyzer import TemporalScoreDetail
        from src.features.text_forensics import LinguisticScoreDetail
        from src.features.network_mapper import NetworkScoreDetail

        meta = MetaScoreDetail(final_score=0.30)
        temp = TemporalScoreDetail(final_score=0.45, monomania_score=0.85, propaganda_risk_score=0.80)
        ling = LinguisticScoreDetail(final_score=0.35)
        net = NetworkScoreDetail(final_score=0.0)

        w_meta, w_temp, w_ling, w_net, notes = compute_dynamic_weights(
            meta_detail=meta,
            temporal_detail=temp,
            linguistic_detail=ling,
            network_detail=net,
            history_hidden=False,
            comment_count=20,
        )

        assert w_temp > 0.30, f"Temporal weight should be boosted for propaganda/agenda focus, got {w_temp}"
        assert any("agenda/propaganda focus" in n for n in notes)


class TestEvidenceHierarchy:

    def test_tier_constants_and_result_structure(self):
        from src.models.bot_classifier import BotAnalysisResult
        res = BotAnalysisResult(
            username="AutoModerator",
            bot_score=0.98,
            label="BOT",
            score_meta=0.1,
            score_temporal=0.8,
            score_linguistic=0.9,
            score_network=0.0,
            evidence_tier="TIER_1_DETERMINISTIC",
            history_recovered_via_osint=True,
            dynamic_weights={"meta": 0.25, "temporal": 0.25, "linguistic": 0.30, "network": 0.20},
        )
        assert res.evidence_tier == "TIER_1_DETERMINISTIC"
        assert res.bot_score == 0.98
        assert res.history_recovered_via_osint is True
        assert res.dynamic_weights["linguistic"] == 0.30

    def test_network_domain_syndication(self):
        from src.features.network_mapper import score_network
        import asyncio
        comments = [
            {"id": "c1", "body": "Check this out: https://t.me/free_crypto_group", "subreddit": "sub1", "submission_id": "s1", "created_utc": 100},
            {"id": "c2", "body": "Join here guys: https://t.me/free_crypto_group", "subreddit": "sub2", "submission_id": "s2", "created_utc": 200},
        ]
        detail = asyncio.run(score_network("spambot123", comments))
        assert detail.syndication_score >= 0.70, "Repeated shortener/telegram syndication should be detected"
        assert any("shortener" in n.lower() or "syndication" in n.lower() for n in detail.notes)

    def test_organic_human_shield(self):
        from src.models.bot_classifier import compute_dynamic_weights
        from src.features.metadata_extractor import MetaScoreDetail
        from src.features.temporal_analyzer import TemporalScoreDetail
        from src.features.text_forensics import LinguisticScoreDetail
        from src.features.network_mapper import NetworkScoreDetail

        # Authentic human with default username pattern (0.20), but organic writing & sleep window
        meta = MetaScoreDetail(final_score=0.20, username_score=0.20, is_default_username=True)
        temp = TemporalScoreDetail(final_score=0.15, shift_pattern_score=0.0, burst_score=0.0)
        ling = LinguisticScoreDetail(final_score=0.05, phrase_score=0.0, template_score=0.0)
        net = NetworkScoreDetail(final_score=0.0)

        w_meta, w_temp, w_ling, w_net, notes = compute_dynamic_weights(
            meta_detail=meta,
            temporal_detail=temp,
            linguistic_detail=ling,
            network_detail=net,
            history_hidden=False,
            comment_count=15,
        )
        raw_score = w_meta * meta.final_score + w_temp * temp.final_score + w_ling * ling.final_score
        assert raw_score < 0.25, f"Authentic human with default username should score safely low, got {raw_score}"

    def test_shared_session_no_type_error(self):
        import asyncio
        from src.collectors.reddit_collector import get_shared_session
        async def _check():
            sess = await get_shared_session()
            assert sess is not None
            assert not sess.closed
        asyncio.run(_check())

    def test_cors_private_network_access_header(self):
        from src.server import make_app
        from aiohttp.test_utils import TestClient, TestServer
        import asyncio
        async def _check():
            app = make_app()
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                resp = await client.options("/api/scan-users", headers={"Access-Control-Request-Private-Network": "true"})
                assert resp.status == 204
                assert resp.headers.get("Access-Control-Allow-Private-Network") == "true"
            finally:
                await client.close()
        asyncio.run(_check())


# ─── Run tests directly ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", __file__, "-v"], cwd=str(Path(__file__).parent.parent))


