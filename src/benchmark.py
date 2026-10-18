"""
src/benchmark.py — Benchmark Evaluation & Regression Test Runner

Evaluates the reddit-bot-identifier engine against a curated dataset
of ground-truth BOT and HUMAN accounts.

Metrics calculated:
  - Accuracy: (TP + TN) / Total
  - Precision: TP / (TP + FP)
  - Recall (Sensitivity): TP / (TP + FN)
  - Specificity: TN / (TN + FP)
  - False Positive Rate (FPR): FP / (FP + TN)  <-- Critical for not banning real users!
  - F1 Score: 2 * (P * R) / (P + R)

Usage:
    python -m src.benchmark
    OR via CLI: botid benchmark
"""
import asyncio
import json
import time
from pathlib import Path
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from src.models.bot_classifier import analyze_user
from src.db import database as db

console = Console()
BENCHMARK_FILE = Path(__file__).resolve().parents[1] / "data" / "benchmark_dataset.json"
HISTORY_FILE = Path(__file__).resolve().parents[1] / "data" / "benchmark_history.json"


@dataclass
class BenchmarkItemResult:
    username: str
    expected: str
    predicted: str
    bot_score: float
    is_correct: bool
    category: str
    notes: list[str]


async def run_benchmark(dataset_path: Path = BENCHMARK_FILE) -> dict:
    """Run full benchmark evaluation on the ground-truth dataset."""
    await db.init_db()

    if not dataset_path.exists():
        console.print(f"[red]Benchmark dataset not found at: {dataset_path}[/red]")
        return {}

    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    accounts = data.get("accounts", [])
    console.print(
        Panel(
            f"[bold cyan]Reddit Bot Identifier — Benchmark Evaluation[/bold cyan]\n"
            f"Testing [bold]{len(accounts)}[/bold] ground-truth accounts across various categories.\n"
            f"Dataset: [dim]{dataset_path.name}[/dim]",
            border_style="cyan",
        )
    )

    results: list[BenchmarkItemResult] = []
    tp = fp = tn = fn = 0

    with console.status("[bold green]Evaluating benchmark accounts...", spinner="dots"):
        for item in accounts:
            username = item["username"]
            expected = item["expected_label"].upper()
            category = item.get("category", "")

            try:
                # Add brief pause between external API lookups to respect rate limits
                await asyncio.sleep(0.5)
                res = await analyze_user(username)
                pred = res.label.upper()

                # SUSPECT is treated as bot-leaning for binary classification purposes
                effective_pred = "BOT" if pred in ("BOT", "SUSPECT") else "HUMAN"
                is_correct = (effective_pred == expected)

                if expected == "BOT":
                    if effective_pred == "BOT":
                        tp += 1
                    else:
                        fn += 1
                else:  # expected == "HUMAN"
                    if effective_pred == "HUMAN":
                        tn += 1
                    else:
                        fp += 1

                results.append(
                    BenchmarkItemResult(
                        username=username,
                        expected=expected,
                        predicted=pred,
                        bot_score=res.bot_score,
                        is_correct=is_correct,
                        category=category,
                        notes=res.all_notes[:3],
                    )
                )

            except Exception as e:
                console.print(f"[red]Error evaluating {username}: {e}[/red]")

    # ── Calculate Metrics ──────────────────────────────────────────────
    total = len(results)
    accuracy = (tp + tn) / max(total, 1)
    precision = tp / max(tp + fp, 1) if (tp + fp) > 0 else 0.0
    recall = tp / max(tp + fn, 1) if (tp + fn) > 0 else 0.0
    specificity = tn / max(tn + fp, 1) if (tn + fp) > 0 else 0.0
    fpr = fp / max(fp + tn, 1) if (fp + tn) > 0 else 0.0
    f1 = 2 * (precision * recall) / max(precision + recall, 1e-9)

    # ── Render Item Table ──────────────────────────────────────────────
    table = Table(
        title="Benchmark Account Results",
        box=box.ROUNDED,
        border_style="cyan",
        show_lines=True,
    )
    table.add_column("Account", style="bold", no_wrap=True)
    table.add_column("Category", style="dim")
    table.add_column("Expected", justify="center")
    table.add_column("Predicted", justify="center")
    table.add_column("Bot Score", justify="right")
    table.add_column("Status", justify="center")

    for r in results:
        status_str = "[green]✓ PASS[/green]" if r.is_correct else "[red]✗ FAIL[/red]"
        color = "red" if r.predicted == "BOT" else ("yellow" if r.predicted == "SUSPECT" else "green")
        table.add_row(
            f"u/{r.username}",
            r.category,
            r.expected,
            f"[{color}]{r.predicted}[/{color}]",
            f"{r.bot_score:.2f}",
            status_str,
        )

    console.print(table)

    # ── Summary Panel ──────────────────────────────────────────────────
    acc_color = "green" if accuracy >= 0.85 else "yellow"
    fpr_color = "green" if fpr <= 0.05 else "red"

    summary_content = (
        f"Total Accounts Tested: [bold]{total}[/bold]  "
        f"(TP: {tp}, TN: {tn}, FP: {fp}, FN: {fn})\n\n"
        f"• Accuracy:      [{acc_color}][bold]{accuracy * 100:.1f}%[/bold][/{acc_color}]\n"
        f"• Precision:     [bold]{precision * 100:.1f}%[/bold]\n"
        f"• Recall (Sens): [bold]{recall * 100:.1f}%[/bold]\n"
        f"• F1 Score:      [bold]{f1:.3f}[/bold]\n"
        f"• False Pos Rate:[{fpr_color}][bold]{fpr * 100:.1f}%[/bold][/{fpr_color}] "
        f"[dim](Lower is better — minimizes real humans wrongly flagged)[/dim]\n"
    )

    console.print(
        Panel(
            summary_content,
            title="[bold]Benchmark Performance Summary[/bold]",
            border_style="cyan",
        )
    )

    # ── Save History ───────────────────────────────────────────────────
    history_entry = {
        "timestamp": time.time(),
        "total": total,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fpr": round(fpr, 4),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
    }

    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    history.append(history_entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    return history_entry


def add_to_benchmark(username: str, expected_label: str, category: str = "in_the_wild", notes: str = "") -> bool:
    """Add or update an account in the ground-truth benchmark dataset."""
    if not BENCHMARK_FILE.exists():
        data = {"description": "Benchmark test dataset for validation", "version": "1.0", "accounts": []}
    else:
        with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

    accounts = data.get("accounts", [])
    existing = [a for a in accounts if a["username"].lower() == username.lower()]
    clean_label = expected_label.upper()

    if existing:
        existing[0]["expected_label"] = clean_label
        existing[0]["category"] = category
        if notes:
            existing[0]["notes"] = notes
    else:
        accounts.append({
            "username": username,
            "expected_label": clean_label,
            "category": category,
            "notes": notes or f"Audited on {time.strftime('%Y-%m-%d')}"
        })

    data["accounts"] = accounts
    with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return True


async def audit_user(username: str, interactive: bool = True):
    """Deep forensic inspection of an account to audit features and compare with ground truth."""
    await db.init_db()
    console.print(f"[cyan]Gathering forensic evidence for u/{username}...[/cyan]\n")

    res = await analyze_user(username)
    score_pct = int(round(res.bot_score * 100))
    color = "red" if res.bot_score >= 0.65 else ("yellow" if res.bot_score >= 0.40 else "green")

    # Layer details
    meta = res.meta_detail
    temp = res.temporal_detail
    ling = res.linguistic_detail
    net = res.network_detail

    # Safe field accessors
    tier_label = getattr(res, "evidence_tier", "TIER_3_BEHAVIORAL_FORENSICS")
    meta_u = getattr(meta, "username_score", 0.0) if meta else 0.0
    meta_d = getattr(meta, "dormancy_score", 0.0) if meta else 0.0
    meta_k = getattr(meta, "karma_score", 0.0) if meta else 0.0

    temp_s = getattr(temp, "sample_size", 0) if temp else 0
    temp_c = getattr(temp, "circadian_score", 0.0) if temp else 0.0
    temp_i = getattr(temp, "iat_score", 0.0) if temp else 0.0
    temp_b = getattr(temp, "burst_score", 0.0) if temp else 0.0
    temp_d = getattr(temp, "dispersion_score", 0.0) if temp else 0.0

    ling_p = getattr(ling, "phrase_score", 0.0) if ling else 0.0
    ling_m = len(getattr(ling, "matches", [])) if ling else 0
    ling_t = getattr(ling, "template_score", 0.0) if ling else 0.0
    ling_l = getattr(ling, "llm_tone_score", 0.0) if ling else 0.0
    ling_r = getattr(ling, "readability_score", 0.0) if ling else 0.0

    net_c = getattr(net, "cooccurrence_score", 0.0) if net else 0.0
    net_r = getattr(net, "reply_pair_score", 0.0) if net else 0.0

    # Evidence Panel
    lines = [
        f"[bold]Account:[/bold] u/{res.username}  │  [bold]Bot Probability:[/bold] [{color}][bold]{score_pct}% ({res.label})[/bold][/{color}]  │  [cyan]{tier_label}[/cyan]",
        f"[dim]History Status: {'Hidden / Incomplete' if res.history_hidden else 'Public comments captured'}[/dim]\n",
        "[bold cyan]1. Account Metadata (S_meta):[/bold cyan]",
        f"   • Score: {res.score_meta:.3f}",
        f"   • Username pattern score: {meta_u:.2f}",
        f"   • Dormancy score: {meta_d:.2f}  │  Karma asymmetry score: {meta_k:.2f}",
        "",
        "[bold cyan]2. Temporal & Posting Velocity (S_temporal):[/bold cyan]",
        f"   • Score: {res.score_temporal:.3f}  (Sample size: {temp_s} comments)",
        f"   • Circadian uniformity: {temp_c:.3f} (1.0 = 24/7 robotic, 0.0 = human sleep trough)",
        f"   • Regularity (IAT CV): {temp_i:.3f}  │  Burst density: {temp_b:.3f}",
        f"   • Subreddit dispersion speed: {temp_d:.3f}",
        "",
        "[bold cyan]3. Linguistic & Content Forensics (S_linguistic):[/bold cyan]",
        f"   • Score: {res.score_linguistic:.3f}",
        f"   • Known bot phrase matches: {ling_p:.3f} ({ling_m} hits)",
        f"   • Template repetition score: {ling_t:.3f} (word/structure overlap across comments)",
        f"   • LLM stylometric tone score: {ling_l:.3f}  │  Readability variance: {ling_r:.3f}",
        "",
        "[bold cyan]4. Network Coordination (S_network):[/bold cyan]",
        f"   • Score: {res.score_network:.3f}",
        f"   • Co-occurrence: {net_c:.3f}  │  Reply pairs: {net_r:.3f}",
        "",
        "[bold yellow]Signals & Detections Flagged:[/bold yellow]",
    ]

    if res.all_notes:
        for n in res.all_notes:
            lines.append(f"   • {n}")
    else:
        lines.append("   • [dim]No suspicious behavioral flags triggered.[/dim]")

    console.print(Panel("\n".join(lines), title="[bold]Deep Behavioral Forensic Audit[/bold]", border_style=color))

    # Show recent comments sample
    recent = await db.get_user_comments(username, limit=3)
    if recent:
        console.print("\n[bold]Sample Recent Comments from u/" + username + ":[/bold]")
        for i, c in enumerate(recent, 1):
            body_snippet = c.get("body", "").replace("\n", " ")[:140]
            console.print(f"  [dim]{i}.[/dim] in [cyan]r/{c.get('subreddit')}[/cyan]: \"{body_snippet}...\"")
        console.print("")

    if interactive:
        from rich.prompt import Prompt
        choice = Prompt.ask(
            "Do you want to add this account to the ground-truth benchmark dataset?",
            choices=["b", "h", "s"],
            default="s"
        )
        if choice in ("b", "h"):
            expected = "BOT" if choice == "b" else "HUMAN"
            cat = Prompt.ask("Enter benchmark category", default="in_the_wild")
            notes = Prompt.ask("Enter audit notes", default="")
            add_to_benchmark(username, expected_label=expected, category=cat, notes=notes)
            console.print(f"[green]✓ Added u/{username} as [{expected}] ({cat}) to benchmark_dataset.json[/green]")
        else:
            console.print("[dim]Skipped benchmark addition.[/dim]")

    return res


if __name__ == "__main__":
    asyncio.run(run_benchmark())

