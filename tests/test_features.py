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

    def test_default_username_high_score(self):
        profile = self._make_profile(username="Cautious_Ad_8492")
        detail = score_metadata(profile)
        assert detail.username_score >= 0.80, "Default username should score high"

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


# ─── Evidence Tier Tests ───────────────────────────────────────────────────────

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
        )
        assert res.evidence_tier == "TIER_1_DETERMINISTIC"
        assert res.bot_score == 0.98


# ─── Run tests directly ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", __file__, "-v"], cwd=str(Path(__file__).parent.parent))


