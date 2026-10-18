"""
config/settings.py — Central configuration loader.
Reads .env and exposes typed settings used across all modules.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class Settings:
    # ── Reddit API ──────────────────────────────────────────────────
    REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    REDDIT_USER_AGENT: str = os.getenv(
        "REDDIT_USER_AGENT", "reddit-bot-identifier/1.0"
    )

    # ── Database ────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "./data/botdb.sqlite")

    # ── Scoring Weights ─────────────────────────────────────────────
    WEIGHT_META: float = float(os.getenv("WEIGHT_META", "0.25"))
    WEIGHT_TEMPORAL: float = float(os.getenv("WEIGHT_TEMPORAL", "0.25"))
    WEIGHT_LINGUISTIC: float = float(os.getenv("WEIGHT_LINGUISTIC", "0.30"))
    WEIGHT_NETWORK: float = float(os.getenv("WEIGHT_NETWORK", "0.20"))

    # ── Thresholds ──────────────────────────────────────────────────
    BOT_THRESHOLD: float = float(os.getenv("BOT_SCORE_THRESHOLD", "0.65"))
    SUSPECT_THRESHOLD: float = float(os.getenv("SUSPECT_SCORE_THRESHOLD", "0.40"))

    # ── Monitoring ──────────────────────────────────────────────────
    MONITOR_SUBREDDITS: str = os.getenv("MONITOR_SUBREDDITS", "all")
    MAX_COMMENT_HISTORY: int = int(os.getenv("MAX_COMMENT_HISTORY", "100"))


settings = Settings()

