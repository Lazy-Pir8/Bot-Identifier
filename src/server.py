"""
src/server.py — Local HTTP API Server for the Browser Extension

Runs an async HTTP API on http://127.0.0.1:8000.
Allows browser extensions, userscripts, or desktop apps to scan Reddit
usernames in real-time and retrieve color-coded bot scores and badge data.

Endpoints:
  GET  /api/status          — Server health and version
  GET  /api/scan/<username> — Scan a single user
  POST /api/scan-users      — Batch scan multiple users concurrently

Usage:
    python -m src.server --port 8000
    OR via CLI: botid serve
"""
import asyncio
import json
import time
from typing import Dict, Any

from aiohttp import web
from rich.console import Console

from src.models.bot_classifier import analyze_user, BotAnalysisResult
from src.db import database as db
from config.settings import settings

console = Console()

# In-memory cache to make page scans blistering fast (<10ms for known/cached users)
# Key: username -> (timestamp, result_dict)
_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
CACHE_TTL = 3600  # 1 hour cache


def _score_to_color(score: float, label: str = "") -> str:
    """Return hex color based on the rich 7-tier score spectrum."""
    if score >= 0.86:
        return "#ff1744"  # Tier 7: Neon Hot Red (Blatant Bot)
    elif score >= 0.75:
        return "#f43f5e"  # Tier 6: Coral Crimson (Likely Bot)
    elif score >= 0.60:
        return "#f97316"  # Tier 5: Vivid Tangerine Orange (High Suspicion)
    elif score >= 0.45:
        return "#eab308"  # Tier 4: Warm Amber / Gold (Moderate Irregularity)
    elif score >= 0.30:
        return "#06b6d4"  # Tier 3: Vibrant Sky Blue (Mild Inconsistencies)
    elif score >= 0.15:
        return "#14b8a6"  # Tier 2: Teal / Cyan-Green (Normal Human)
    else:
        return "#10b981"  # Tier 1: Pure Emerald Green (Pristine Human)


def _format_result(res: BotAnalysisResult) -> Dict[str, Any]:
    color = _score_to_color(res.bot_score, res.label)
    return {
        "username": res.username,
        "score": res.bot_score,
        "label": res.label,
        "color": color,
        "scores": {
            "meta": res.score_meta,
            "temporal": res.score_temporal,
            "linguistic": res.score_linguistic,
            "network": res.score_network,
        },
        "notes": res.all_notes[:4],
        "history_hidden": res.history_hidden,
        "timestamp": res.analyzed_at,
    }


# ─── CORS Middleware ──────────────────────────────────────────────────────────
@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=200)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


# ─── Request Handlers ─────────────────────────────────────────────────────────
async def handle_status(request: web.Request) -> web.Response:
    stats = await db.get_stats()
    return web.json_response({
        "status": "online",
        "service": "reddit-bot-identifier-api",
        "version": "1.0",
        "cache_entries": len(_CACHE),
        "db_stats": stats,
    })


async def handle_scan_single(request: web.Request) -> web.Response:
    username = request.match_info.get("username", "").strip()
    if not username:
        return web.json_response({"error": "Username required"}, status=400)

    now = time.time()
    # Check cache
    if username in _CACHE and (now - _CACHE[username][0]) < CACHE_TTL:
        return web.json_response(_CACHE[username][1])

    # Check local DB
    existing_user = await db.get_user(username)
    if existing_user and existing_user.get("last_analyzed") and (now - existing_user["last_analyzed"]) < CACHE_TTL:
        cached_result = {
            "username": username,
            "score": existing_user["bot_score"],
            "label": existing_user["label"],
            "color": _score_to_color(existing_user["bot_score"], existing_user["label"]),
            "scores": {
                "meta": existing_user.get("score_meta", 0),
                "temporal": existing_user.get("score_temporal", 0),
                "linguistic": existing_user.get("score_linguistic", 0),
                "network": existing_user.get("score_network", 0),
            },
            "notes": json.loads(existing_user.get("notes") or "[]") if isinstance(existing_user.get("notes"), str) else [],
            "history_hidden": bool(existing_user.get("is_hidden", 0)),
            "timestamp": existing_user["last_analyzed"],
        }
        _CACHE[username] = (now, cached_result)
        return web.json_response(cached_result)

    # Live Analysis
    try:
        res = await analyze_user(username)
        formatted = _format_result(res)
        _CACHE[username] = (now, formatted)
        return web.json_response(formatted)
    except Exception as e:
        return web.json_response({"error": str(e), "username": username}, status=500)


async def handle_scan_batch(request: web.Request) -> web.Response:
    """Analyze a batch of usernames in parallel."""
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON payload"}, status=400)

    usernames = payload.get("usernames", [])
    if not isinstance(usernames, list):
        return web.json_response({"error": "usernames must be an array of strings"}, status=400)

    # Sanitize and deduplicate
    clean_users = list(dict.fromkeys(
        u.replace("u/", "").strip() for u in usernames if isinstance(u, str) and u.strip()
    ))[:50]  # Cap at 50 per batch

    now = time.time()
    results: Dict[str, Any] = {}
    users_to_fetch = []

    # 1. Immediate resolution from Memory Cache
    for u in clean_users:
        if u in _CACHE and (now - _CACHE[u][0]) < CACHE_TTL:
            results[u] = _CACHE[u][1]
        else:
            users_to_fetch.append(u)

    # 2. Check SQLite DB for remaining
    remaining = []
    for u in users_to_fetch:
        existing = await db.get_user(u)
        if existing and existing.get("last_analyzed") and (now - existing["last_analyzed"]) < CACHE_TTL:
            formatted = {
                "username": u,
                "score": existing["bot_score"],
                "label": existing["label"],
                "color": _score_to_color(existing["bot_score"], existing["label"]),
                "scores": {
                    "meta": existing.get("score_meta", 0),
                    "temporal": existing.get("score_temporal", 0),
                    "linguistic": existing.get("score_linguistic", 0),
                    "network": existing.get("score_network", 0),
                },
                "notes": [],
                "history_hidden": bool(existing.get("is_hidden", 0)),
                "timestamp": existing["last_analyzed"],
            }
            results[u] = formatted
            _CACHE[u] = (now, formatted)
        else:
            remaining.append(u)

    # 3. Analyze remaining concurrently (concurrency limited to 5 to protect rate limits)
    if remaining:
        sem = asyncio.Semaphore(5)

        async def _bounded_analyze(uname: str):
            async with sem:
                try:
                    res = await analyze_user(uname)
                    formatted = _format_result(res)
                    _CACHE[uname] = (time.time(), formatted)
                    return uname, formatted
                except Exception as e:
                    return uname, {"username": uname, "error": str(e), "score": 0.0, "label": "UNKNOWN"}

        tasks = [_bounded_analyze(u) for u in remaining]
        batch_out = await asyncio.gather(*tasks)
        for uname, out in batch_out:
            results[uname] = out

    return web.json_response({"results": results})


def make_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/scan/{username}", handle_scan_single)
    app.router.add_post("/api/scan-users", handle_scan_batch)
    return app


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the API server synchronously."""
    asyncio.run(db.init_db())
    app = make_app()
    console.print(
        f"[bold green]Starting Reddit Bot Identifier Local API Server...[/bold green]\n"
        f"URL: [cyan]http://{host}:{port}[/cyan]\n"
        f"API status: [cyan]http://{host}:{port}/api/status[/cyan]\n"
        f"Browser Extension Ready! (Press Ctrl+C to stop)"
    )
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    start_server()

