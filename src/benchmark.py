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


if __name__ == "__main__":
    asyncio.run(run_benchmark())

