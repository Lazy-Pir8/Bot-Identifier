"""
src/collectors/reddit_collector.py

AsyncPRAW-based Reddit data collector.

Responsibilities:
  - Authenticate with Reddit API (read-only, no login needed)
  - Fetch Redditor profile metadata (karma, age, name)
  - Fetch up to N recent comments from a user (returns [] if hidden/banned)
  - Stream live comments from specified subreddits in real-time

Fallback strategy for hidden histories:
  - Reddit API (AsyncPRAW) is attempted first
  - If result is empty or raises NotFound/Forbidden, we return what we have
    from our own DB (captured live comments we've seen from that user)
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import asyncpraw
import asyncpraw.exceptions

from config.settings import settings


@dataclass
class RedditorProfile:
    username: str
    created_utc: float = 0.0          # account creation timestamp
    comment_karma: int = 0
    link_karma: int = 0
    is_suspended: bool = False
    is_mod: bool = False
    has_verified_email: bool = False
    history_hidden: bool = False       # True if Reddit hid their history
    recent_comments: list[dict] = field(default_factory=list)
    fetch_error: Optional[str] = None


@dataclass
class LiveComment:
    id: str
    username: str
    subreddit: str
    body: str
    created_utc: float
    score: int
    permalink: str
    submission_id: str
    parent_id: str          # t1_ = reply to comment, t3_ = top-level


def _make_reddit() -> asyncpraw.Reddit:
    return asyncpraw.Reddit(
        client_id=settings.REDDIT_CLIENT_ID,
        client_secret=settings.REDDIT_CLIENT_SECRET,
        user_agent=settings.REDDIT_USER_AGENT,
    )


async def fetch_redditor_profile(
    username: str,
    max_comments: int = 100,
) -> RedditorProfile:
    """
    Fetch a Redditor's public profile and recent comment history.
    Handles suspended, shadowbanned, or history-hidden accounts gracefully.
    """
    profile = RedditorProfile(username=username)
    has_credentials = (
        bool(settings.REDDIT_CLIENT_ID)
        and settings.REDDIT_CLIENT_ID != "your_client_id_here"
        and settings.REDDIT_CLIENT_ID != "your_client_id_from_under_the_app_name"
    )

    if not has_credentials:
        return await _fetch_from_arctic_shift(username, max_comments)

    reddit = _make_reddit()
    try:
        redditor = await reddit.redditor(username)
        # Force fetch — raises NotFound for shadowbanned/deleted
        await redditor.load()

        profile.created_utc = getattr(redditor, "created_utc", 0.0)
        profile.comment_karma = getattr(redditor, "comment_karma", 0)
        profile.link_karma = getattr(redditor, "link_karma", 0)
        profile.is_suspended = getattr(redditor, "is_suspended", False)
        profile.has_verified_email = getattr(redditor, "has_verified_email", False)

        # Try fetching comment history
        comments = []
        try:
            async for comment in redditor.comments.new(limit=max_comments):
                comments.append({
                    "id": comment.id,
                    "body": comment.body,
                    "subreddit": str(comment.subreddit),
                    "created_utc": comment.created_utc,
                    "score": comment.score,
                    "permalink": comment.permalink,
                    "submission_id": comment.link_id,
                    "parent_id": comment.parent_id,
                })
        except Exception:
            # History is hidden, private, or throttled
            profile.history_hidden = True

        profile.recent_comments = comments

    except asyncpraw.exceptions.InvalidURL:
        profile.fetch_error = "invalid_username"
    except Exception as e:
        err = str(e).lower()
        if "404" in err or "not found" in err:
            profile.fetch_error = "not_found_or_shadowbanned"
        elif "403" in err or "forbidden" in err:
            profile.history_hidden = True
            profile.fetch_error = "forbidden"
        else:
            # Fall back to Arctic Shift
            return await _fetch_from_arctic_shift(username, max_comments)
    finally:
        try:
            await reddit.close()
        except Exception:
            pass

    return profile


async def _fetch_from_arctic_shift(username: str, max_comments: int = 100) -> RedditorProfile:
    """Fetch user comments and metadata from Arctic Shift public archive (no API keys required)."""
    import aiohttp
    profile = RedditorProfile(username=username)
    url = f"https://arctic-shift.photon-reddit.com/api/comments/search?author={username}&limit={max_comments}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw_comments = data.get("data", [])
                    comments = []
                    for c in raw_comments:
                        comments.append({
                            "id": c.get("id", ""),
                            "body": c.get("body", ""),
                            "subreddit": c.get("subreddit", ""),
                            "created_utc": float(c.get("created_utc", 0.0)),
                            "score": int(c.get("score", 0)),
                            "permalink": c.get("permalink", ""),
                            "submission_id": c.get("link_id", ""),
                            "parent_id": c.get("parent_id", ""),
                        })
                    profile.recent_comments = comments
                    if comments:
                        profile.created_utc = min(c["created_utc"] for c in comments) - 86400 * 30
                        profile.comment_karma = sum(max(c["score"], 0) for c in comments)
    except Exception as e:
        profile.fetch_error = f"arctic_shift_error: {e}"
    return profile


async def stream_subreddit_comments(
    subreddits: str = "all",
    skip_existing: bool = True,
) -> AsyncIterator[LiveComment]:
    """
    Yields LiveComment objects in real-time.
    Uses AsyncPRAW if configured; otherwise seamlessly streams from Arctic Shift.
    """
    has_credentials = (
        bool(settings.REDDIT_CLIENT_ID)
        and settings.REDDIT_CLIENT_ID != "your_client_id_here"
        and settings.REDDIT_CLIENT_ID != "your_client_id_from_under_the_app_name"
    )

    if has_credentials:
        try:
            reddit = _make_reddit()
            subreddit = await reddit.subreddit(subreddits)
            async for comment in subreddit.stream.comments(
                skip_existing=skip_existing, pause_after=0
            ):
                if comment is None:
                    await asyncio.sleep(0.1)
                    continue
                author = comment.author
                if author is None:
                    continue
                yield LiveComment(
                    id=comment.id,
                    username=str(author),
                    subreddit=str(comment.subreddit),
                    body=comment.body,
                    created_utc=comment.created_utc,
                    score=comment.score,
                    permalink=comment.permalink,
                    submission_id=comment.link_id,
                    parent_id=comment.parent_id,
                )
            await reddit.close()
            return
        except Exception:
            pass  # Fall back to Arctic Shift below

    # ── Fallback: Stream from Arctic Shift (no credentials needed) ─────
    import aiohttp
    seen_ids = set()
    base_url = "https://arctic-shift.photon-reddit.com/api/comments/search?limit=25"
    if subreddits and subreddits != "all":
        # First subreddit in list if comma-separated
        target_sub = subreddits.split(",")[0].strip()
        base_url += f"&subreddit={target_sub}"

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(base_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        items = payload.get("data", [])
                        for item in reversed(items):
                            cid = item.get("id")
                            if not cid or cid in seen_ids:
                                continue
                            seen_ids.add(cid)
                            if len(seen_ids) > 2000:
                                seen_ids.clear()
                            author = item.get("author")
                            if not author or author in ("[deleted]", "[removed]"):
                                continue
                            yield LiveComment(
                                id=cid,
                                username=author,
                                subreddit=item.get("subreddit", ""),
                                body=item.get("body", ""),
                                created_utc=float(item.get("created_utc", time.time())),
                                score=int(item.get("score", 0)),
                                permalink=item.get("permalink", ""),
                                submission_id=item.get("link_id", ""),
                                parent_id=item.get("parent_id", ""),
                            )
        except Exception:
            pass
        await asyncio.sleep(2.0)

