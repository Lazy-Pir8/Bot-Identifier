"""
src/monitor.py — Real-time Reddit Comment Stream Monitor

This is the main engine that:
  1. Streams live comments from configured subreddits
  2. For each comment, immediately checks the body against bot phrases
  3. Stores the comment in the DB for historical tracking
  4. Triggers a full user analysis if:
     - The user hasn't been analyzed in the last 6 hours, OR
     - The comment itself matches a bot phrase (immediate re-analyze)
  5. Displays live results in the terminal with Rich formatting

Usage:
    python -m src.monitor
    OR via CLI: botid monitor
"""
import asyncio
import time
import os
import sys
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box

from config.settings import settings
from src.collectors.reddit_collector import stream_subreddit_comments, LiveComment
from src.features.text_forensics import score_single_comment
from src.models.bot_classifier import analyze_user
from src.db import database as db

console = Console()

# How often (seconds) we re-analyze a user even without phrase match
REANALYZE_INTERVAL = 6 * 3600   # 6 hours
# Brief cooldown between API calls per-user to avoid rate limits
PER_USER_COOLDOWN = 60           # 1 minute

# In-memory cache: username -> last_analyzed_timestamp
_last_analyzed: dict[str, float] = {}
_analysis_queue: asyncio.Queue = asyncio.Queue(maxsize=500)


def _label_color(label: str) -> str:
    return {"BOT": "red", "SUSPECT": "yellow", "HUMAN": "green"}.get(label, "white")


def _score_bar(score: float, width: int = 20) -> str:
    filled = int(score * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "red" if score > 0.65 else "yellow" if score > 0.40 else "green"
    return f"[{color}]{bar}[/{color}] {score:.2f}"


async def _analysis_worker() -> None:
    """Background worker that processes the analysis queue sequentially."""
    while True:
        try:
            username, comment_body, comment_id = await _analysis_queue.get()
            try:
                result = await analyze_user(
                    username,
                    trigger_comment_body=comment_body,
                    trigger_comment_id=comment_id,
                )
                _last_analyzed[username] = time.time()

                # Pretty print result
                label_color = _label_color(result.label)
                score_display = _score_bar(result.bot_score)

                panel_content = (
                    f"[bold]u/{result.username}[/bold]  "
                    f"[{label_color}][bold]{result.label}[/bold][/{label_color}]\n"
                    f"Score: {score_display}\n"
                    f"  Meta:       {_score_bar(result.score_meta, 12)}\n"
                    f"  Temporal:   {_score_bar(result.score_temporal, 12)}\n"
                    f"  Linguistic: {_score_bar(result.score_linguistic, 12)}\n"
                    f"  Network:    {_score_bar(result.score_network, 12)}\n"
                )

                if result.all_notes:
                    panel_content += "\n[dim]Signals:[/dim]\n"
                    for note in result.all_notes[:5]:
                        panel_content += f"  [dim]• {note}[/dim]\n"

                if result.trigger_comment:
                    snippet = result.trigger_comment[:120].replace("\n", " ")
                    panel_content += f"\n[dim]Comment: \"{snippet}\"[/dim]"

                border = "red" if result.label == "BOT" else (
                    "yellow" if result.label == "SUSPECT" else "green"
                )
                console.print(
                    Panel(
                        panel_content,
                        border_style=border,
                        expand=False,
                        padding=(0, 1),
                    )
                )

            except Exception as e:
                console.print(f"[red]Analysis error for u/{username}: {e}[/red]")
            finally:
                _analysis_queue.task_done()
        except asyncio.CancelledError:
            break


async def _should_analyze(username: str, phrase_hit: bool) -> bool:
    """Decide whether to (re)analyze this user."""
    now = time.time()
    last = _last_analyzed.get(username, 0)
    if phrase_hit:
        # Immediate re-analyze if phrase hit and cooldown has passed
        return (now - last) > PER_USER_COOLDOWN
    # Otherwise, only re-analyze if interval elapsed
    return (now - last) > REANALYZE_INTERVAL


async def run_monitor() -> None:
    """Main monitoring loop."""
    await db.init_db()

    console.print(
        Panel(
            f"[bold green]Reddit Bot Identifier — Live Monitor[/bold green]\n"
            f"Streaming: [cyan]{settings.MONITOR_SUBREDDITS}[/cyan]\n"
            f"Bot threshold: [red]{settings.BOT_THRESHOLD}[/red]  "
            f"Suspect threshold: [yellow]{settings.SUSPECT_THRESHOLD}[/yellow]\n"
            f"DB: [dim]{settings.DB_PATH}[/dim]",
            border_style="cyan",
        )
    )

    # Start analysis worker in background
    worker = asyncio.create_task(_analysis_worker())
    comment_count = 0
    analyzed_count = 0
    phrase_hits = 0

    try:
        async for live_comment in stream_subreddit_comments(
            subreddits=settings.MONITOR_SUBREDDITS
        ):
            comment_count += 1

            # ── Fast phrase check (no API call needed) ────────────────
            phrase_score, phrase_matches = score_single_comment(
                live_comment.body, live_comment.id
            )
            phrase_hit = phrase_score > 0.5

            if phrase_hit:
                phrase_hits += 1
                match_label = phrase_matches[0].phrase if phrase_matches else "unknown"
                console.print(
                    f"[yellow]⚡ PHRASE HIT[/yellow] u/{live_comment.username} "
                    f"in r/{live_comment.subreddit} — "
                    f"[dim]\"{match_label[:60]}\"[/dim]"
                )

            # ── Store comment in DB ───────────────────────────────────
            await db.upsert_user(live_comment.username)
            await db.save_comment(
                comment_id=live_comment.id,
                username=live_comment.username,
                subreddit=live_comment.subreddit,
                body=live_comment.body,
                created_utc=live_comment.created_utc,
                score=live_comment.score,
                submission_id=live_comment.submission_id,
                parent_id=live_comment.parent_id,
                is_bot_phrase=phrase_hit,
                matched_phrase=phrase_matches[0].phrase if phrase_matches else None,
            )

            # ── Status ticker (every 100 comments) ───────────────────
            if comment_count % 100 == 0:
                console.print(
                    f"[dim]── {comment_count} comments seen │ "
                    f"{phrase_hits} phrase hits │ "
                    f"{analyzed_count} analyzed ──[/dim]"
                )

            # ── Queue full analysis if warranted ─────────────────────
            if await _should_analyze(live_comment.username, phrase_hit):
                if not _analysis_queue.full():
                    await _analysis_queue.put(
                        (live_comment.username, live_comment.body, live_comment.id)
                    )
                    analyzed_count += 1

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping monitor...[/yellow]")
    except Exception as e:
        console.print(f"[red]Stream error: {e}[/red]")
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        stats = await db.get_stats()
        console.print(
            Panel(
                f"Session Summary\n"
                f"Comments seen:  {comment_count}\n"
                f"Phrase hits:    {phrase_hits}\n"
                f"Users analyzed: {analyzed_count}\n"
                f"Total BOTs in DB: {stats.get('bot', 0)}\n"
                f"Total SUSPECTS:   {stats.get('suspect', 0)}\n"
                f"Total HUMANS:     {stats.get('human', 0)}",
                border_style="cyan",
            )
        )
