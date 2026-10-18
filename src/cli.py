"""
src/cli.py — Command Line Interface

Commands:
    botid monitor              — Start real-time stream monitoring
    botid scan <username>      — Analyze a single Reddit username
    botid list-bots            — Show all BOTs and SUSPECTs in DB
    botid stats                — Show database statistics
    botid add-phrase <phrase>  — Add a new phrase to the bot phrase DB
    botid setup                — First-time setup guide

Run: python -m src.cli --help
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import settings
from src.db import database as db

console = Console()


def _label_color(label: str) -> str:
    return {"BOT": "red", "SUSPECT": "yellow", "HUMAN": "green"}.get(label, "dim")


def _score_bar(score: float, width: int = 16) -> str:
    filled = int(score * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "red" if score > 0.65 else "yellow" if score > 0.40 else "green"
    return f"[{color}]{bar}[/{color}] {score:.2f}"


@click.group()
def cli():
    """Reddit Bot Identifier — Real-time Reddit bot detection engine."""
    pass


# ─── botid monitor ────────────────────────────────────────────────────────────
@cli.command()
@click.option("--subreddits", "-s", default=None,
              help="Comma-separated subreddits to monitor (default: from .env)")
def monitor(subreddits: str | None):
    """Start real-time stream monitoring of Reddit comments."""
    if subreddits:
        settings.MONITOR_SUBREDDITS = subreddits

    has_credentials = (
        bool(settings.REDDIT_CLIENT_ID)
        and settings.REDDIT_CLIENT_ID not in ("your_client_id_here", "your_client_id_from_under_the_app_name")
    )
    if not has_credentials:
        console.print(
            "[cyan]ℹ Reddit API keys not configured. Running in open mode via public Reddit stream (Arctic Shift).[/cyan]\n"
        )

    from src.monitor import run_monitor
    asyncio.run(run_monitor())


# ─── botid scan ───────────────────────────────────────────────────────────────
@cli.command()
@click.argument("username")
@click.option("--json-out", "-j", is_flag=True, help="Output raw JSON")
def scan(username: str, json_out: bool):
    """Analyze a single Reddit username and print the full report."""
    has_credentials = (
        bool(settings.REDDIT_CLIENT_ID)
        and settings.REDDIT_CLIENT_ID not in ("your_client_id_here", "your_client_id_from_under_the_app_name")
    )
    if not has_credentials and not json_out:
        console.print(
            "[dim]ℹ Using public open archive for user history (no API keys required).[/dim]"
        )

    async def _run():
        await db.init_db()
        from src.models.bot_classifier import analyze_user
        console.print(f"[cyan]Analyzing u/{username}...[/cyan]")
        result = await analyze_user(username)
        return result

    result = asyncio.run(_run())

    if json_out:
        print(json.dumps({
            "username": result.username,
            "label": result.label,
            "bot_score": result.bot_score,
            "score_meta": result.score_meta,
            "score_temporal": result.score_temporal,
            "score_linguistic": result.score_linguistic,
            "score_network": result.score_network,
            "history_hidden": result.history_hidden,
            "notes": result.all_notes,
        }, indent=2))
        return

    label_color = _label_color(result.label)

    console.print(Panel(
        f"[bold]Scan Report: u/{result.username}[/bold]\n"
        f"Label:      [{label_color}][bold]{result.label}[/bold][/{label_color}]\n"
        f"Bot Score:  {_score_bar(result.bot_score)}\n"
        f"\n"
        f"[bold]Layer Breakdown:[/bold]\n"
        f"  S_meta       (username, age, karma):    {_score_bar(result.score_meta)}\n"
        f"  S_temporal   (circadian, IAT, burst):   {_score_bar(result.score_temporal)}\n"
        f"  S_linguistic (phrases, LLM tone, style): {_score_bar(result.score_linguistic)}\n"
        f"  S_network    (co-occurrence, replies):  {_score_bar(result.score_network)}\n"
        + (f"\n[yellow]⚠ History hidden — limited temporal/network data[/yellow]\n"
           if result.history_hidden else "")
        + ("\n[bold]Signals detected:[/bold]\n"
           + "\n".join(f"  • {n}" for n in result.all_notes[:8])
           if result.all_notes else "\n[dim]No specific bot signals detected.[/dim]"),
        border_style=label_color,
        title=f"[bold]Reddit Bot Identifier[/bold]",
    ))


# ─── botid list-bots ─────────────────────────────────────────────────────────
@cli.command("list-bots")
@click.option("--limit", "-n", default=30, help="Number of results to show")
@click.option("--label", "-l", default=None,
              type=click.Choice(["BOT", "SUSPECT", "HUMAN"], case_sensitive=False),
              help="Filter by label")
def list_bots(limit: int, label: str | None):
    """List all BOTs and SUSPECTs in the database."""
    async def _run():
        await db.init_db()
        return await db.list_bots(limit=limit)
    bots = asyncio.run(_run())

    if label:
        bots = [b for b in bots if b["label"] == label.upper()]

    if not bots:
        console.print("[dim]No bots found in database yet. Run[/dim] [bold]botid monitor[/bold] [dim]to start.[/dim]")
        return

    table = Table(
        title=f"Bot Database — Top {len(bots)} Results",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=True,
    )
    table.add_column("Username", style="bold", no_wrap=True)
    table.add_column("Label", justify="center")
    table.add_column("Bot Score", justify="right")
    table.add_column("Meta", justify="right")
    table.add_column("Temporal", justify="right")
    table.add_column("Linguistic", justify="right")
    table.add_column("Network", justify="right")
    table.add_column("Analyses", justify="center")

    for b in bots:
        lc = _label_color(b["label"])
        table.add_row(
            f"u/{b['username']}",
            f"[{lc}]{b['label']}[/{lc}]",
            f"{b['bot_score']:.2f}",
            f"{b.get('score_meta', 0):.2f}" if b.get('score_meta') is not None else "—",
            f"{b.get('score_temporal', 0):.2f}" if b.get('score_temporal') is not None else "—",
            f"{b.get('score_linguistic', 0):.2f}" if b.get('score_linguistic') is not None else "—",
            f"{b.get('score_network', 0):.2f}" if b.get('score_network') is not None else "—",
            str(b.get("analysis_count", 0)),
        )

    console.print(table)


# ─── botid stats ─────────────────────────────────────────────────────────────
@cli.command()
def stats():
    """Show database statistics."""
    async def _run():
        await db.init_db()
        return await db.get_stats()
    s = asyncio.run(_run())

    console.print(Panel(
        f"[bold]Database Statistics[/bold]\n\n"
        f"[red]BOTs identified:     {s.get('bot', 0)}[/red]\n"
        f"[yellow]SUSPECTs:           {s.get('suspect', 0)}[/yellow]\n"
        f"[green]HUMANs cleared:     {s.get('human', 0)}[/green]\n"
        f"[dim]Unknown (new):      {s.get('unknown', 0)}[/dim]\n"
        f"\n"
        f"Total comments captured: {s.get('total_comments', 0):,}\n"
        f"Bot phrase hits:         {s.get('phrase_hits', 0):,}\n"
        f"DB path: {settings.DB_PATH}",
        border_style="cyan",
        title="Reddit Bot Identifier",
    ))


# ─── botid add-phrase ─────────────────────────────────────────────────────────
@cli.command("add-phrase")
@click.argument("phrase")
@click.option("--type", "-t", "phrase_type", default="exact",
              type=click.Choice(["exact", "fuzzy", "pattern"]),
              help="Phrase match type")
def add_phrase(phrase: str, phrase_type: str):
    """Add a new phrase to the bot phrase database."""
    async def _run():
        await db.init_db()
        await db.add_phrase(phrase, phrase_type)
    asyncio.run(_run())
    console.print(f"[green]✓ Added [{phrase_type}] phrase: \"{phrase}\"[/green]")


# ─── botid serve ─────────────────────────────────────────────────────────────
@cli.command("serve")
@click.option("--host", "-h", default="127.0.0.1", help="Host address to bind")
@click.option("--port", "-p", default=8000, type=int, help="Port to listen on")
def serve(host: str, port: int):
    """Start the local HTTP API server for browser extensions."""
    from src.server import start_server
    start_server(host=host, port=port)


# ─── botid benchmark ─────────────────────────────────────────────────────────
@cli.command("benchmark")
@click.option("--dataset", "-d", default=None, help="Path to custom benchmark dataset JSON")
def benchmark(dataset: str | None):
    """Run benchmark evaluation on the ground-truth bot dataset."""
    from src.benchmark import run_benchmark, BENCHMARK_FILE
    ds_path = Path(dataset) if dataset else BENCHMARK_FILE
    asyncio.run(run_benchmark(ds_path))


# ─── botid scan-page ─────────────────────────────────────────────────────────
@cli.command("scan-page")
@click.argument("target")
@click.option("--limit", "-n", default=25, help="Maximum number of commenters to scan")
def scan_page(target: str, limit: int):
    """Scan all commenters on a Reddit post URL or submission ID."""
    import re
    import requests

    # Extract submission id if full URL is given
    sub_match = re.search(r"comments\/([a-z0-9]+)", target, re.IGNORECASE)
    sub_id = sub_match.group(1) if sub_match else target.replace("t3_", "").strip()

    console.print(f"[cyan]Fetching commenters from Reddit submission: {sub_id}...[/cyan]")

    # Query public archive for comments on this submission
    url = f"https://arctic-shift.photon-reddit.com/api/comments/search?link_id=t3_{sub_id}&limit=100"
    authors = []
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            comments = r.json().get("data", [])
            for c in comments:
                a = c.get("author")
                if a and a not in ("[deleted]", "[removed]") and a not in authors:
                    authors.append(a)
    except Exception:
        pass

    if not authors:
        # Fallback to Reddit public JSON
        try:
            r2 = requests.get(
                f"https://www.reddit.com/comments/{sub_id}.json",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=6,
            )
            if r2.status_code == 200 and isinstance(r2.json(), list) and len(r2.json()) > 1:
                children = r2.json()[1].get("data", {}).get("children", [])
                for child in children:
                    a = child.get("data", {}).get("author")
                    if a and a not in ("[deleted]", "[removed]") and a not in authors:
                        authors.append(a)
        except Exception:
            pass

    if not authors:
        console.print("[red]Could not retrieve comments for this submission. Verify ID/URL or network.[/red]")
        return

    authors = authors[:limit]
    console.print(f"[green]Discovered {len(authors)} distinct authors. Running multi-factor analysis...[/green]\n")

    async def _analyze_all():
        await db.init_db()
        from src.models.bot_classifier import analyze_user
        results = []
        for a in authors:
            try:
                res = await analyze_user(a)
                results.append(res)
            except Exception:
                pass
        return results

    results = asyncio.run(_analyze_all())

    table = Table(
        title=f"Page Scan Report: Submission {sub_id}",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=True,
    )
    table.add_column("Author", style="bold", no_wrap=True)
    table.add_column("Verdict", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Meta", justify="right")
    table.add_column("Temporal", justify="right")
    table.add_column("Linguistic", justify="right")
    table.add_column("Key Signals", style="dim")

    for res in sorted(results, key=lambda x: x.bot_score, reverse=True):
        lc = _label_color(res.label)
        table.add_row(
            f"u/{res.username}",
            f"[{lc}][bold]{res.label}[/bold][/{lc}]",
            _score_bar(res.bot_score, width=10),
            f"{res.score_meta:.2f}",
            f"{res.score_temporal:.2f}",
            f"{res.score_linguistic:.2f}",
            res.all_notes[0] if res.all_notes else "Clean profile",
        )

    console.print(table)


# ─── botid setup ─────────────────────────────────────────────────────────────
@cli.command()
def setup():
    """Interactive first-time setup guide."""
    console.print(Panel(
        "[bold cyan]Reddit Bot Identifier — Setup Guide[/bold cyan]\n\n"
        "You need a Reddit API application (free, read-only).\n\n"
        "[bold]Steps:[/bold]\n"
        "1. Go to: [link=https://www.reddit.com/prefs/apps]https://www.reddit.com/prefs/apps[/link]\n"
        "2. Click [bold]'Create App'[/bold] or [bold]'Create Another App'[/bold]\n"
        "3. Choose [bold]'script'[/bold] as the app type\n"
        "4. Fill in any name and redirect URI: http://localhost:8080\n"
        "5. Copy your [bold]client_id[/bold] (under the app name) and [bold]client_secret[/bold]\n\n"
        "Then copy [bold].env.example[/bold] to [bold].env[/bold] and fill in:\n"
        "  REDDIT_CLIENT_ID=<your client_id>\n"
        "  REDDIT_CLIENT_SECRET=<your client_secret>\n"
        "  REDDIT_USER_AGENT=reddit-bot-identifier/1.0 by u/<your_username>\n\n"
        "Run: [bold]botid monitor[/bold] to start streaming!",
        border_style="cyan",
    ))


if __name__ == "__main__":
    cli()

