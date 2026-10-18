"""
src/features/text_forensics.py — Layer 3: Linguistic & Content Forensics

Scores comments on:
  1. Phrase matching vs bot_phrases DB (exact + fuzzy + regex)
  2. LLM stylometric markers (hedging, uniform structure, formality)
  3. Readability uniformity (humans vary; LLMs are strangely consistent)
  4. Generic/universal comment detection (safe upvote-farming)
  5. Repost/duplicate detection within our captured comment DB

Returns S_linguistic in [0.0, 1.0] — higher = more bot-like.
"""
import re
import math
from dataclasses import dataclass, field
from typing import Optional

import jellyfish
import textstat

from data.bot_phrases import EXACT_PHRASES, FUZZY_PHRASES, PATTERN_STRINGS

# Pre-compile all regex patterns once at import time
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in PATTERN_STRINGS]

# Similarity thresholds
_EXACT_THRESHOLD = 0.92   # Jaro-Winkler for 'exact' bucket (very close)
_FUZZY_THRESHOLD = 0.88   # Jaro-Winkler for fuzzy bucket

# LLM tone markers (compiled for fast multi-comment scanning)
_LLM_MARKERS = [
    re.compile(r"(?i)great (question|insight|point|observation)"),
    re.compile(r"(?i)hope (this|that) helps"),
    re.compile(r"(?i)feel free to (ask|reach out|dm)"),
    re.compile(r"(?i)happy to help"),
    re.compile(r"(?i)it'?s important to (remember|consider|note)"),
    re.compile(r"(?i)at the end of the day.{0,20}(depends|comes down)"),
    re.compile(r"(?i)while (both|all) (sides?|parties?|arguments?) (have|make)"),
    re.compile(r"(?i)i (completely |fully )?(understand|see) where you'?re coming from"),
    re.compile(r"(?i)let me know if you (have|need) (any )?(more|further)"),
]


@dataclass
class PhraseMatch:
    phrase: str
    match_type: str   # exact / fuzzy / pattern / llm_marker
    similarity: float
    comment_id: Optional[str] = None


@dataclass
class LinguisticScoreDetail:
    phrase_score: float = 0.0
    llm_tone_score: float = 0.0
    readability_score: float = 0.0
    generic_score: float = 0.0
    final_score: float = 0.0
    matches: list[PhraseMatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _jaro(a: str, b: str) -> float:
    """Jaro-Winkler similarity between two strings."""
    return jellyfish.jaro_winkler_similarity(a, b)


def score_single_comment(
    body: str,
    comment_id: Optional[str] = None,
) -> tuple[float, list[PhraseMatch]]:
    """
    Score a single comment body for bot phrase presence.

    Returns:
        (phrase_score [0,1], list of PhraseMatch objects)
    """
    matches: list[PhraseMatch] = []
    norm = _normalize(body)

    # ── Exact phrase matching (substring containment) ──────────────────
    for phrase in EXACT_PHRASES:
        norm_p = _normalize(phrase)
        if norm_p and norm_p in norm:
            matches.append(PhraseMatch(phrase, "exact", 1.0, comment_id))
            break  # one exact match is enough

    # ── Fuzzy phrase matching (windowed / substring similarity) ───────
    if not matches:
        for phrase in FUZZY_PHRASES:
            norm_p = _normalize(phrase)
            if norm_p in norm:
                matches.append(PhraseMatch(phrase, "fuzzy", 1.0, comment_id))
                break
            # If not exact substring, check sliding window of similar length
            p_len = len(norm_p)
            if p_len > 10 and len(norm) >= p_len:
                words_p = norm_p.split()
                words_body = norm.split()
                w_len = len(words_p)
                for i in range(max(len(words_body) - w_len + 1, 1)):
                    window = " ".join(words_body[i:i + w_len])
                    sim = _jaro(window, norm_p)
                    if sim >= _FUZZY_THRESHOLD:
                        matches.append(PhraseMatch(phrase, "fuzzy", sim, comment_id))
                        break
            if matches:
                break

    # ── Regex pattern matching ─────────────────────────────────────────
    for pattern in _COMPILED_PATTERNS:
        m = pattern.search(body)
        if m:
            matches.append(
                PhraseMatch(m.group(0)[:80], "pattern", 1.0, comment_id)
            )
            break

    # ── LLM marker matching ────────────────────────────────────────────
    llm_hits = sum(1 for p in _LLM_MARKERS if p.search(body))
    if llm_hits >= 2:
        matches.append(
            PhraseMatch(
                f"{llm_hits} LLM tone markers", "llm_marker", min(llm_hits / 4, 1.0), comment_id
            )
        )

    # Derive phrase score for this single comment
    if not matches:
        return 0.0, []
    best_sim = max(m.similarity for m in matches)
    return min(best_sim, 1.0), matches


def score_linguistics(
    comments: list[dict],
    new_comment_body: Optional[str] = None,
    new_comment_id: Optional[str] = None,
) -> LinguisticScoreDetail:
    """
    Compute the full linguistic bot score for a user.

    Args:
        comments: Historical comment dicts from DB or Reddit API.
                  Each must have at minimum: {'body': str, 'id': str}
        new_comment_body: The triggering real-time comment (may not be in comments yet).
        new_comment_id: ID of the triggering comment.

    Returns:
        LinguisticScoreDetail with component scores and a final [0,1] score.
    """
    detail = LinguisticScoreDetail()

    all_bodies: list[tuple[str, Optional[str]]] = [
        (c["body"], c.get("id")) for c in comments if c.get("body")
    ]
    if new_comment_body:
        all_bodies.insert(0, (new_comment_body, new_comment_id))

    if not all_bodies:
        return detail

    # ── 1. Phrase matching across all comments ─────────────────────────
    phrase_scores = []
    for body, cid in all_bodies:
        s, matches = score_single_comment(body, cid)
        phrase_scores.append(s)
        detail.matches.extend(matches)

    # Weighted: most-recent triggering comment counts more
    if phrase_scores:
        # Exponential decay weight — first (most recent) weighs most
        weights = [math.exp(-0.1 * i) for i in range(len(phrase_scores))]
        total_w = sum(weights)
        detail.phrase_score = sum(
            s * w for s, w in zip(phrase_scores, weights)
        ) / total_w
    if detail.phrase_score > 0.5:
        detail.notes.append(
            f"Bot phrase matched in {sum(1 for s in phrase_scores if s > 0)} "
            f"of {len(phrase_scores)} comments"
        )

    # ── 2. LLM tone score ─────────────────────────────────────────────
    # Count how many comments have ≥2 LLM markers → fraction
    llm_flagged = 0
    for body, _ in all_bodies:
        hits = sum(1 for p in _LLM_MARKERS if p.search(body))
        if hits >= 2:
            llm_flagged += 1
    detail.llm_tone_score = llm_flagged / max(len(all_bodies), 1)
    if detail.llm_tone_score > 0.3:
        detail.notes.append(
            f"LLM tone markers in {llm_flagged}/{len(all_bodies)} comments"
        )

    # ── 3. Readability uniformity ─────────────────────────────────────
    # Humans vary wildly in readability; LLMs stay in a narrow band.
    # Using Flesch Reading Ease — humans range 0–100, LLMs cluster ~60-70.
    readability_score = 0.0
    if len(all_bodies) >= 5:
        scores_fre = []
        for body, _ in all_bodies[:50]:  # cap at 50 for speed
            try:
                fre = textstat.flesch_reading_ease(body)
                if 0 <= fre <= 100:
                    scores_fre.append(fre)
            except Exception:
                pass
        if len(scores_fre) >= 5:
            import statistics
            std_fre = statistics.stdev(scores_fre)
            # Human std typically 20-40; LLMs often < 8
            if std_fre < 5:
                readability_score = 0.9
                detail.notes.append(
                    f"Suspiciously uniform readability scores (std={std_fre:.1f})"
                )
            elif std_fre < 10:
                readability_score = 0.5
    detail.readability_score = readability_score

    # ── 4. Generic/universal comment detection ─────────────────────────
    # Short comments that could apply anywhere are scored higher.
    generic_count = 0
    for body, _ in all_bodies:
        stripped = body.strip()
        # Very short AND no personal pronouns → likely generic karma bait
        if (
            len(stripped) < 80
            and not re.search(r"\b(i|my|me|we|our|you|your)\b", stripped, re.I)
            and not re.search(r"\b(but|because|however|although)\b", stripped, re.I)
        ):
            generic_count += 1
    detail.generic_score = generic_count / max(len(all_bodies), 1)
    if detail.generic_score > 0.6:
        detail.notes.append(
            f"High proportion of generic short comments: {generic_count}/{len(all_bodies)}"
        )

    # ── Final weighted average for S_linguistic ────────────────────────
    detail.final_score = (
        0.40 * detail.phrase_score
        + 0.25 * detail.llm_tone_score
        + 0.20 * detail.readability_score
        + 0.15 * detail.generic_score
    )
    return detail
