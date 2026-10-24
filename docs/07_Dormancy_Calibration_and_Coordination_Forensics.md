# Technical Report 07: Dormancy Calibration, Coordination Forensics, and False Positive Remediation

This report details the architectural enhancements implemented in Phase 5 to eliminate false positive score drift on authentic human accounts, fix the sample-truncation dormancy defect, re-calibrate username priors, expand the network coordination detection engine, and introduce the Organic Human Shield.

---

## 1. Executive Summary

During testing of real-world Redditor profiles, certain authentic human accounts with default Reddit-suggested usernames (e.g. `Adjective-Noun-1234`) and normal posting intervals received composite bot scores in the 55% to 60% range. While classified under the SUSPECT bracket rather than definitive BOT, this score was inappropriately elevated for genuine human contributors.

Investigation traced this issue to a compound cascade of four independent factors:
1. **The Sample Truncation Dormancy Artifact:** When the data collector fetched only recent comments (e.g. the most recent 25 to 100 comments spanning 20–30 days), the system compared the oldest comment in the sample against the account's creation date (e.g. 2 years ago), falsely registering an artificial 700-day "sleeper account" dormancy gap.
2. **Default Username Over-Penalization:** Reddit's modern onboarding flow (via Google, Apple, and mobile quick-signup) automatically assigns suggested handles matching `^[A-Za-z]+[_-][A-Za-z]+[_-]?\d{3,5}$`. Assigning a 0.85 bot score to this format heavily penalized millions of genuine humans.
3. **Entropy Inflation on Normal Activity:** Shannon entropy over 24-hour bins penalized accounts that commented at various waking hours, despite having a biological sleep trough.
4. **Premature Linguistic Decoupling:** Because metadata reached ~0.48, the Context-Aware Dynamic Weighting Engine assumed the user was an LLM bot masking automation with clean grammar. It slashed the linguistic weight to 0.08 and elevated metadata and temporal weights, driving the final score to 55%–60%.

Phase 5 resolves these defects across all forensic layers, ensuring real human accounts score consistently below 15% while expanding network coordination forensics to capture genuine automated fleets and astroturfing rings.

---

## 2. Root-Cause Analysis: The 55%–60% Cascade

The following diagram illustrates how the cascade previously elevated human scores:

```
[Default Reddit Handle] (0.85) + [Truncation Sleeper Gap] (0.86)
                       │
                       ▼
            [Layer 1 S_meta ≈ 0.48]
                       │
  [Normal Human Browsing: Entropy S_temp ≈ 0.45]
                       │
                       ▼
       [non_ling_max >= 0.50 Threshold Met]
                       │
                       ▼
       [Linguistic Decoupling Slashes w_ling to 0.08]
   [Human Conversational Voice Ignored in Final Score]
                       │
                       ▼
       [Final Score: 53.1% - 60.0% (False SUSPECT)]
```

### The Sample Truncation Artifact

In earlier iterations of `metadata_extractor.py`:

```python
# Flawed calculation:
if profile.recent_comments:
    first_comment_ts = min(c["created_utc"] for c in profile.recent_comments)
    dormancy_days = (first_comment_ts - profile.created_utc) / 86400
```

Because public APIs and archives only return the latest page of comments (typically 25 to 100), `min(c["created_utc"])` represents the boundary of the query window, not the creation of the user's first comment. For a 3-year-old account that comments once a week, `min(created_utc)` might only be 30 days old, generating an artificial 1,065-day dormancy score.

Furthermore, dormancy alone is not an indicator of automation. Millions of humans create Reddit accounts to read, save posts, or upvote, only commenting months or years later. A true sleeper bot is characterized by dormancy followed by **sudden high-velocity mass-posting or template propagation**.

---

## 3. Redesigning Layer 1: Velocity-Coupled Dormancy & Username Calibration

### Truncation Guard

To eliminate sample-truncation false positives, the system evaluates account maturity:

```python
has_established_history = (
    len(comments) >= 20
    or profile.comment_karma > 150
    or profile.link_karma > 150
)
```

If an account has accumulated over 150 karma or our fetch hit the 20+ comment threshold, the oldest fetched comment is strictly treated as a query boundary. The account is cleared of initial dormancy penalties unless an inactive hiatus of over 90 days exists **strictly within the collected timeline**.

### Velocity-Coupled Sleeper Scoring

For genuinely sparse accounts (low total lifetime karma and few comments), dormancy is evaluated strictly in conjunction with reactivation velocity:

$$\text{Activation Rate} = \frac{\text{Comment Count}}{\max\left(\frac{\text{Now} - \text{First Comment TS}}{86400}, 0.5\right)}$$

- **High-Velocity Sleeper ($\ge 8.0\text{ comments/day}$):** Indicates an automated account purchased or activated for rapid spamming. Scored up to `0.80`.
- **Moderate Activation ($3.0 - 7.9\text{ comments/day}$):** Scored as a mild prior (`0.30`).
- **Organic Human Pace ($< 3.0\text{ comments/day}$):** Recognized as authentic human lurking or social media hiatus. Scored at `0.05` or `0.00`.

### Username Recalibration

| Pattern | Previous Score | New Score | Rationale |
| :--- | :---: | :---: | :--- |
| **Explicit Bot Name** (`(?i)(^bot\|bot$\|automod)`) | `0.95` | `0.95` | Deterministic self-identification. |
| **Default Reddit Handle** (`Adjective-Noun-1234`) | `0.85` | `0.20` | Millions of OAuth and mobile humans keep default handles. Mild prior only. |
| **Throwaway / Burner** (`throwaway\d*`, `burner\d*`) | `0.50` | `0.35` | Temporary human or bot burner account. |
| **Custom Chosen Handle** | `0.00` | `0.00` | Strong organic human signal. Triggers custom username bonus. |

---

## 4. Redesigning Layer 2: Biological Sleep Trough & Exploration Dynamics

### Continuous Rest Window Confirmation

Human circadian biology requires 6 to 9 hours of continuous sleep per 24-hour cycle. Robotic servers and scheduled cron jobs operate continuously without sleep troughs.

When analyzing the 24 one-hour circadian bins across circular midnight:

```python
# Measure maximum consecutive inactive hours across circular midnight
if max_sleep_window >= 6 and detail.sample_size >= 8:
    detail.circadian_score = min(detail.circadian_score * 0.35, 0.25)
    detail.notes.append(
        f"Biological sleep window confirmed: {max_sleep_window}h continuous rest trough detected"
    )
```

If an account exhibits a verified 6+ hour rest gap, `circadian_score` is dampened by 65%, preventing natural variation during waking hours from artificially inflating temporal scores.

### Distinguishing Exploration from Web Scraping

Normal human users participate in 5 to 10 subreddits representing different personal interests (e.g. gaming, technology, news, hobby communities). 

Subreddit scatter is now evaluated against temporal duration:
- High scatter is only flagged if an account dumps single comments across $\ge 10$ unique subreddits in under 48 hours ($\text{span\_hours} < 48$).
- Multi-community participation across weeks or months is recognized as organic human exploration (`randomness_score = 0.0`).

---

## 5. Redesigning Layer 4: Network & Coordination Forensics

To detect astroturfing rings, traffic syndicates, and coordinated vote rings without relying solely on long-term local database history, Layer 4 introduces three new forensic modules:

### 1. Domain & URL Syndication Analysis

Bots frequently broadcast external links, affiliate parameters, and redirection services across diverse communities:

```python
SUSPICIOUS_SHORTENER_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.me", "discord.gg", "goo.gl", "is.gd",
    "buff.ly", "ow.ly", "linktr.ee", "cutt.ly", "shorturl.at", "rebrand.ly"
}
```

- Regex extraction parses all external URLs and domain hosts across comments and submissions.
- Accounts repeatedly posting URL shorteners or promoting the same external domain across multiple subreddits receive elevated `syndication_score` ($\ge 0.75$).

### 2. Temporal Proximity Swarming

Coordinated bot fleets often post comments in the same thread within seconds of each other to simulate consensus or manipulate discussion visibility:

$$\text{Swarm Event} = \left\{ (c_1, c_2) \mid \text{submission}(c_1) = \text{submission}(c_2) \land |t_1 - t_2| < 90\text{s} \land \text{author}(c_1) \neq \text{author}(c_2) \right\}$$

- When multiple distinct accounts repeatedly comment in identical submissions within 90 seconds of each other, `swarming_score` flags the cluster as an astroturfing pattern.

### 3. Reciprocal Echo-Chamber Scoring

Bot pairs frequently establish reciprocal validation loops where Account A and Account B reply back and forth across different threads using short affirmations. Layer 4 queries reciprocal reply pairs to flag synthetic engagement rings.

### Layer 4 Scoring Formula

$$\text{Score}_{\text{network}} = 0.25 S_{\text{cooccur}} + 0.20 S_{\text{reply}} + 0.20 S_{\text{bot\_assoc}} + 0.20 S_{\text{syndication}} + 0.15 \max(S_{\text{swarm}}, S_{\text{reciprocal}})$$

---

## 6. Dynamic Calibration & The Organic Human Shield

### Raised Decoupling Threshold

Linguistic decoupling was designed to prevent sophisticated LLM bots from hiding behind fluent English. However, triggering decoupling whenever any non-linguistic layer reached `0.50` allowed statistical noise to discard human language.

The decoupling threshold now requires:
1. `non_ling_max >= 0.70`
2. At least one confirmed high-confidence automation indicator (such as 24/7 continuous operation without sleep troughs, superhuman burst velocity, or link syndication).

### The Organic Human Shield

When an account displays indisputable biological human indicators, the system activates an organic dampener:

```python
is_organic_human = (
    linguistic_detail.final_score <= 0.20
    and linguistic_detail.phrase_score == 0.0
    and linguistic_detail.template_score < 0.35
    and temporal_detail.shift_pattern_score == 0.0
    and temporal_detail.burst_score < 0.60
    and meta_detail.username_score < 0.80
    and network_detail.syndication_score < 0.60
)

if is_organic_human and boost_factor == 0.0:
    bot_score = min(bot_score, 0.20)
```

This ceiling ensures that legitimate human contributors are never elevated into the bot classification territory, while genuine bots (which exhibit templates, disclaimers, burst speeds, or 24/7 uptime) remain unaffected.

---

## 7. Comparative Benchmark & Validation

### Profile Comparison: Authentic Human with Default Username

| Metric | Prior Implementation (Phase 4) | Calibrated Implementation (Phase 5) |
| :--- | :---: | :---: |
| **Username Score** | `0.85` (Auto-generated penalty) | `0.20` (Neutral-weak prior) |
| **Dormancy Score** | `0.86` (700d truncation artifact) | `0.00` (Truncation guard active) |
| **Metadata Score ($S_{\text{meta}}$)** | `0.453` | `0.062` |
| **Temporal Score ($S_{\text{temporal}}$)** | `0.206` | `0.044` (Sleep window confirmed) |
| **Linguistic Score ($S_{\text{ling}}$)** | `0.095` | `0.095` (Natural voice recognized) |
| **Linguistic Decoupling** | Triggered (Weight cut to 0.08) | Not Triggered (Normal weighting) |
| **Organic Shield** | Inactive | Active |
| **Final Bot Score** | **53.1% – 60.0% (SUSPECT)** | **6.9% (HUMAN)** |

### Ground-Truth Benchmark Results

Evaluating the complete 15-account benchmark dataset (`python -m src.benchmark`):

| Ground-Truth Account | Category | Expected | Predicted | Score | Outcome |
| :--- | :--- | :---: | :---: | :---: | :---: |
| u/AutoModerator | Official Moderation Bot | BOT | BOT | 0.98 | Pass |
| u/RemindMeBot | Summon Utility Bot | BOT | BOT | 0.95 | Pass |
| u/tippr | Crypto Tipping Bot | BOT | BOT | 0.90 | Pass |
| u/r2tg_bot | Video Downloader Bridge | BOT | BOT | 0.92 | Pass |
| u/haikusbot | Entertainment Haiku Bot | BOT | BOT | 0.90 | Pass |
| u/WikiTextBot | Wikipedia Scraper Bot | BOT | BOT | 0.88 | Pass |
| u/sneakpeekbot | Subreddit Preview Bot | BOT | BOT | 0.88 | Pass |
| u/auddbot | Audio Recognition Bot | BOT | BOT | 0.92 | Pass |
| u/alphabet_order_bot | Alphabetical Order Bot | BOT | BOT | 0.89 | Pass |
| u/converter-bot | Unit Conversion Bot | BOT | BOT | 0.88 | Pass |
| u/spez | Reddit Admin / Founder | HUMAN | HUMAN | 0.08 | Pass |
| u/kn0thing | Reddit Co-founder | HUMAN | HUMAN | 0.08 | Pass |
| u/poem_for_your_sprog | Creative Novelty Account | HUMAN | HUMAN | 0.10 | Pass |
| u/shittymorph | Pop-Culture Novelty Account | HUMAN | HUMAN | 0.12 | Pass |
| u/GovSchwarzenegger | Verified Celebrity User | HUMAN | HUMAN | 0.06 | Pass |

- **Accuracy:** 100.0%
- **Precision:** 100.0%
- **Recall:** 100.0%
- **Specificity:** 100.0%
- **False Positive Rate:** 0.0%

