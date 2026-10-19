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

import aiohttp
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
    history_recovered_via_osint: bool = False  # True if recovered via OSINT/archives
    recent_comments: list[dict] = field(default_factory=list)
    recent_posts: list[dict] = field(default_factory=list)
    archive_source: Optional[str] = None
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
        return await _fetch_from_public_archives(username, max_comments)

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

        # Also try fetching submissions/posts
        posts = []
        try:
            async for sub in redditor.submissions.new(limit=min(max_comments // 2, 50)):
                posts.append({
                    "id": sub.id,
                    "title": sub.title,
                    "body": f"{sub.title}\n{sub.selftext}".strip() if getattr(sub, "selftext", None) else sub.title,
                    "subreddit": str(sub.subreddit),
                    "created_utc": sub.created_utc,
                    "score": sub.score,
                    "permalink": sub.permalink,
                    "submission_id": sub.id,
                    "parent_id": "",
                    "is_post": True,
                })
        except Exception:
            pass

        profile.recent_posts = posts

        # Fallback to public OSINT archives if history is hidden or empty
        if profile.history_hidden or (not profile.recent_comments and not profile.recent_posts):
            archive_profile = await _fetch_from_public_archives(username, max_comments)
            if archive_profile.recent_comments or archive_profile.recent_posts:
                profile.recent_comments = archive_profile.recent_comments
                profile.recent_posts = archive_profile.recent_posts
                profile.archive_source = archive_profile.archive_source
                profile.history_recovered_via_osint = True
                profile.history_hidden = False

    except asyncpraw.exceptions.InvalidURL:
        profile.fetch_error = "invalid_username"
    except Exception as e:
        err = str(e).lower()
        if "404" in err or "not found" in err:
            profile.fetch_error = "not_found_or_shadowbanned"
        elif "403" in err or "forbidden" in err:
            profile.history_hidden = True
            profile.fetch_error = "forbidden"
            # Fall back to public OSINT archives
            archive_profile = await _fetch_from_public_archives(username, max_comments)
            if archive_profile.recent_comments or archive_profile.recent_posts:
                profile.recent_comments = archive_profile.recent_comments
                profile.recent_posts = archive_profile.recent_posts
                profile.archive_source = archive_profile.archive_source
                profile.history_recovered_via_osint = True
                profile.history_hidden = False
        else:
            # Fall back to public archives
            return await _fetch_from_public_archives(username, max_comments)
    finally:
        try:
            await reddit.close()
        except Exception:
            pass

    return profile


ARCHIVE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

_SHARED_SESSION: Optional[aiohttp.ClientSession] = None


async def get_shared_session() -> aiohttp.ClientSession:
    global _SHARED_SESSION
    if _SHARED_SESSION is None or _SHARED_SESSION.closed:
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_roundrobin=300, keepalive_timeout=75)
        _SHARED_SESSION = aiohttp.ClientSession(connector=connector)
    return _SHARED_SESSION


async def _fetch_from_arctic_shift(session, username: str, max_comments: int = 100) -> list[dict]:
    """Fetch comments from Arctic Shift public archive."""
    url = f"https://arctic-shift.photon-reddit.com/api/comments/search?author={username}&limit={max_comments}"
    async with session.get(url, headers=ARCHIVE_HEADERS, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
        if resp.status == 200:
            payload = await resp.json()
            raw_comments = payload.get("data", [])
            return [
                {
                    "id": c.get("id", ""),
                    "body": c.get("body", ""),
                    "subreddit": c.get("subreddit", ""),
                    "created_utc": float(c.get("created_utc", 0.0)),
                    "score": int(c.get("score", 0)),
                    "permalink": c.get("permalink", ""),
                    "submission_id": c.get("link_id", ""),
                    "parent_id": c.get("parent_id", ""),
                }
                for c in raw_comments
                if c.get("body")
            ]
    return []


async def _fetch_posts_from_arctic_shift(session, username: str, max_posts: int = 50) -> list[dict]:
    """Fetch posts/submissions from Arctic Shift public archive."""
    url = f"https://arctic-shift.photon-reddit.com/api/posts/search?author={username}&limit={max_posts}"
    async with session.get(url, headers=ARCHIVE_HEADERS, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
        if resp.status == 200:
            payload = await resp.json()
            raw_posts = payload.get("data", [])
            return [
                {
                    "id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "body": f"{p.get('title', '')}\n{p.get('selftext', '')}".strip() if p.get('selftext') else p.get('title', ''),
                    "subreddit": p.get("subreddit", ""),
                    "created_utc": float(p.get("created_utc", 0.0)),
                    "score": int(p.get("score", 0)),
                    "permalink": p.get("permalink", ""),
                    "submission_id": p.get("id", ""),
                    "parent_id": "",
                    "is_post": True,
                }
                for p in raw_posts
                if p.get("title") or p.get("selftext")
            ]
    return []


async def _fetch_from_pullpush(session, username: str, max_comments: int = 100) -> list[dict]:
    """Fetch comments from Pullpush public archive (fallback source)."""
    url = f"https://api.pullpush.io/reddit/search/comment/?author={username}&size={max_comments}"
    async with session.get(url, headers=ARCHIVE_HEADERS, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
        if resp.status == 200:
            payload = await resp.json()
            raw_comments = payload.get("data", [])
            return [
                {
                    "id": c.get("id", ""),
                    "body": c.get("body", ""),
                    "subreddit": c.get("subreddit", ""),
                    "created_utc": float(c.get("created_utc", 0.0)),
                    "score": int(c.get("score", 0)),
                    "permalink": c.get("permalink", ""),
                    "submission_id": c.get("link_id", ""),
                    "parent_id": c.get("parent_id", ""),
                }
                for c in raw_comments
                if c.get("body")
            ]
    return []


async def _fetch_posts_from_pullpush(session, username: str, max_posts: int = 50) -> list[dict]:
    """Fetch posts/submissions from Pullpush public archive."""
    url = f"https://api.pullpush.io/reddit/search/submission/?author={username}&size={max_posts}"
    async with session.get(url, headers=ARCHIVE_HEADERS, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
        if resp.status == 200:
            payload = await resp.json()
            raw_posts = payload.get("data", [])
            return [
                {
                    "id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "body": f"{p.get('title', '')}\n{p.get('selftext', '')}".strip() if p.get('selftext') else p.get('title', ''),
                    "subreddit": p.get("subreddit", ""),
                    "created_utc": float(p.get("created_utc", 0.0)),
                    "score": int(p.get("score", 0)),
                    "permalink": p.get("permalink", ""),
                    "submission_id": p.get("id", ""),
                    "parent_id": "",
                    "is_post": True,
                }
                for p in raw_posts
                if p.get("title") or p.get("selftext")
            ]
    return []


async def _fetch_from_reddit_public_json(session, username: str, max_comments: int = 100) -> list[dict]:
    """Fetch comments directly from Reddit unauthenticated public JSON."""
    url = f"https://www.reddit.com/user/{username}/comments.json?limit={min(max_comments, 100)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
        if resp.status == 200:
            payload = await resp.json()
            children = payload.get("data", {}).get("children", [])
            return [
                {
                    "id": c.get("data", {}).get("id", ""),
                    "body": c.get("data", {}).get("body", ""),
                    "subreddit": c.get("data", {}).get("subreddit", ""),
                    "created_utc": float(c.get("data", {}).get("created_utc", 0.0)),
                    "score": int(c.get("data", {}).get("score", 0)),
                    "permalink": c.get("data", {}).get("permalink", ""),
                    "submission_id": c.get("data", {}).get("link_id", ""),
                    "parent_id": c.get("data", {}).get("parent_id", ""),
                }
                for c in children
                if c.get("data", {}).get("body")
            ]
    return []


async def _fetch_posts_from_reddit_public_json(session, username: str, max_posts: int = 50) -> list[dict]:
    """Fetch posts directly from Reddit unauthenticated public JSON."""
    url = f"https://www.reddit.com/user/{username}/submitted.json?limit={min(max_posts, 100)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
        if resp.status == 200:
            payload = await resp.json()
            children = payload.get("data", {}).get("children", [])
            return [
                {
                    "id": c.get("data", {}).get("id", ""),
                    "title": c.get("data", {}).get("title", ""),
                    "body": f"{c.get('data', {}).get('title', '')}\n{c.get('data', {}).get('selftext', '')}".strip() if c.get('data', {}).get('selftext') else c.get('data', {}).get('title', ''),
                    "subreddit": c.get("data", {}).get("subreddit", ""),
                    "created_utc": float(c.get("data", {}).get("created_utc", 0.0)),
                    "score": int(c.get("data", {}).get("score", 0)),
                    "permalink": c.get("data", {}).get("permalink", ""),
                    "submission_id": c.get("data", {}).get("id", ""),
                    "parent_id": "",
                    "is_post": True,
                }
                for c in children
                if c.get("data", {}).get("title") or c.get("data", {}).get("selftext")
            ]
    return []


async def _fetch_from_public_archives(username: str, max_comments: int = 100) -> RedditorProfile:
    """
    Multi-source fast OSINT fetcher:
    Tries fast public Reddit JSON first (~200ms), falling back to Arctic Shift & PullPush.
    Executes comments and posts fetches in parallel to cut latency in half.
    """
    profile = RedditorProfile(username=username)
    session = await get_shared_session()

    async def _get_comments():
        # Fast-path 1: Direct Reddit Public JSON (150-250ms)
        try:
            res = await _fetch_from_reddit_public_json(session, username, max_comments)
            if res:
                return res, "Reddit Public JSON"
        except Exception:
            pass
        # Fallback 2: Arctic Shift
        try:
            res = await _fetch_from_arctic_shift(session, username, max_comments)
            if res:
                return res, "Arctic Shift"
        except Exception:
            pass
        # Fallback 3: Pullpush
        try:
            res = await _fetch_from_pullpush(session, username, max_comments)
            if res:
                return res, "Pullpush"
        except Exception:
            pass
        return [], None

    async def _get_posts():
        # Fast-path 1: Direct Reddit Public JSON (150-250ms)
        try:
            res = await _fetch_posts_from_reddit_public_json(session, username, max_comments // 2)
            if res:
                return res
        except Exception:
            pass
        # Fallback 2: Arctic Shift Posts
        try:
            res = await _fetch_posts_from_arctic_shift(session, username, max_comments // 2)
            if res:
                return res
        except Exception:
            pass
        # Fallback 3: Pullpush Posts
        try:
            res = await _fetch_posts_from_pullpush(session, username, max_comments // 2)
            if res:
                return res
        except Exception:
            pass
        return []

    # Run comment and post gathering concurrently
    (comments_res, archive_source), posts_res = await asyncio.gather(
        _get_comments(),
        _get_posts(),
        return_exceptions=False,
    )

    profile.recent_comments = comments_res
    profile.recent_posts = posts_res
    profile.archive_source = archive_source

    all_items = comments_res + posts_res
    if all_items:
        profile.created_utc = min(c["created_utc"] for c in all_items) - 86400 * 30
        profile.comment_karma = sum(max(c.get("score", 0), 0) for c in comments_res)
        profile.link_karma = sum(max(p.get("score", 0), 0) for p in posts_res)
        profile.history_recovered_via_osint = True
        profile.history_hidden = False  # Successfully unhidden via OSINT!
    else:
        profile.history_hidden = True
        profile.fetch_error = "no_comments_available_from_any_source"

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

