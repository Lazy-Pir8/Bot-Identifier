# Reddit Bot Identifier

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: Pytest](https://img.shields.io/badge/tests-pytest-green.svg)](tests/)

A real-time Reddit bot detection and monitoring engine. It can monitor live pages on reddit, specific users/subreddits and from their behaviour identify whether the user is a human or a bot. It creates a score on a 0-100 scale where a higher score means BOT. The score is calculated using a multi-factor behavioural algorithm along with some pre-determined constraints.

> It doesn't require any API keys or credentials, as it uses Reddit API for data lookup and falls back to Third Party Sources (e.g. Arctic Shift and PullPush) which provide public stream/archive without any credentials.

---

## 📑 Documentation Suite

Comprehensive technical, architectural, and design reports are located in the [`docs/`](docs/) directory:

- **[00 System Architecture & Overview](docs/00_Architecture_and_System_Overview.md)** — Core design philosophy, high-level component diagrams, and architectural trade-offs.
- **[01 Data Pipeline & Multi-Archive OSINT](docs/01_Data_Pipeline_and_Multi_Archive_OSINT.md)** — Tri-source fallback (AsyncPRAW $\to$ Arctic Shift $\to$ PullPush $\to$ DOM scraping), dual comment + post ingestion, and SQLite WAL caching.
- **[02 Context-Aware Dynamic Weighting Engine](docs/02_Context_Aware_Dynamic_Weighting_Engine.md)** — Overcoming static linear dilution, linguistic decoupling for LLM bots, and strict unity normalization ($\sum w_i = 1.000$).
- **[03 Four-Layer Behavioral Forensics](docs/03_Four_Layer_Behavioral_Forensics.md)** — Deep dive into Metadata ($S_{\text{meta}}$), Temporal Dynamics ($S_{\text{temporal}}$), Text Forensics ($S_{\text{linguistic}}$), and Network Mapping ($S_{\text{network}}$).
- **[04 Three-Tier Evidence Hierarchy & Latency](docs/04_Three_Tier_Evidence_Hierarchy_and_Latency.md)** — Deterministic vs Watchlist vs Forensics, $O(1)$ in-memory RAM registry, and resolution of the "0.98 bug".
- **[05 Browser Extension & DOM Architecture](docs/05_Browser_Extension_and_DOM_Architecture.md)** — Manifest V3 content script, anti-false-positive DOM parsing, single-badge UI, 7-tier continuous color gradient, and global floating tooltip.
- **[06 Benchmark Methodology & Validation](docs/06_Benchmark_Methodology_and_Validation.md)** — 15 ground-truth accounts across 6 archetypes, edge-case bot audits (`tippr`, `r2tg_bot`), and 100% precision metrics.

---

## 🏗 System Architecture

```
reddit-bot-identifier/
├── config/
│   ├── __init__.py
│   └── settings.py              # Environment configuration loader
├── data/
│   ├── __init__.py
│   ├── bot_phrases.py           # 56 exact, 15 fuzzy & 20 regex bot patterns
│   ├── benchmark_dataset.json   # 15 curated ground-truth benchmark accounts
│   └── benchmark_history.json   # Benchmark run metrics history
├── docs/                        # Complete technical & architectural documentation suite
│   ├── 00_Architecture_and_System_Overview.md
│   ├── 01_Data_Pipeline_and_Multi_Archive_OSINT.md
│   ├── 02_Context_Aware_Dynamic_Weighting_Engine.md
│   ├── 03_Four_Layer_Behavioral_Forensics.md
│   ├── 04_Three_Tier_Evidence_Hierarchy_and_Latency.md
│   ├── 05_Browser_Extension_and_DOM_Architecture.md
│   ├── 06_Benchmark_Methodology_and_Validation.md
│   ├── Benchmark Methodology & Real World Validation.md
│   ├── Bot Behavior Patterns & Indentification.md
│   ├── Bot Identifer Architecture & Design.md
│   └── Changes and Improvements.md
├── extension/                   # Chromium MV3 browser extension for live scanning
│   ├── manifest.json
│   ├── content.js               # MutationObserver DOM scanner & badge injector
│   ├── styles.css               # 7-tier continuous color gradient & tooltip styling
│   ├── popup.html & popup.js    # Extension status popup
│   └── README.md
├── src/
│   ├── __init__.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   └── reddit_collector.py  # Multi-archive pipeline: AsyncPRAW + Arctic Shift + PullPush
│   ├── features/
│   │   ├── __init__.py
│   │   ├── metadata_extractor.py  # Layer 1 (S_meta): Username regex, dormancy, karma
│   │   ├── temporal_analyzer.py   # Layer 2 (S_temporal): Circadian entropy, 24/7 sleep absence, monomania
│   │   ├── text_forensics.py      # Layer 3 (S_linguistic): Substring phrases, LLM style, template check
│   │   └── network_mapper.py      # Layer 4 (S_network): Co-occurrence & reply pairs
│   ├── models/
│   │   ├── __init__.py
│   │   └── bot_classifier.py    # Dynamic Weighting Engine + 3-Tier Hierarchy + Bayesian boost
│   ├── db/
│   │   ├── __init__.py
│   │   └── database.py          # Async SQLite database layer (WAL mode)
│   ├── monitor.py               # Real-time comment stream monitor & queue worker
│   ├── server.py                # Local REST API server (aiohttp) for browser extension
│   ├── benchmark.py             # Ground-truth benchmark evaluation harness & audit tool
│   └── cli.py                   # Click-based command-line interface
├── tests/
│   ├── __init__.py
│   └── test_features.py         # Full pytest test suite (23 unit tests)
├── logs/                        # Run logs directory
├── .env.example                 # Template for custom credentials & weights
├── .gitignore                   # Ignores .env, *.sqlite, caches & logs
├── pyproject.toml               # Package build configuration
├── requirements.txt             # Pip dependency manifest
├── LICENSE                      # MIT License
└── README.md
```

---

## 🧮 Multi-Factor Scoring Formula

$$\text{Score}_{\text{bot}} = w_1 S_{\text{meta}} + w_2 S_{\text{temporal}} + w_3 S_{\text{linguistic}} + w_4 S_{\text{network}}$$

| Layer | Standard Weight | What It Evaluates |
| :--- | :---: | :--- |
| **$S_{\text{meta}}$** | `0.25` | Explicit bot usernames (`bot`, `automod`), auto-generated naming (`Adjective-Noun-1234`), sleeper account dormancy gaps ($>90\text{d}$ age with sudden activation), and karma asymmetry (extreme link/comment ratio, superhuman karma rate). |
| **$S_{\text{temporal}}$** | `0.25` | Circadian 24h Shannon entropy, 24/7 continuous operation without sleep trough ($<4\text{h}$ rest gap across circular midnight), timer interval regularity ($CV < 0.15$), sliding-window burst rates, rapid multi-sub hopping, topical monomania ($\ge 80\%$ single-sub concentration), and political propaganda cluster scoring. |
| **$S_{\text{linguistic}}$** | `0.30` | Substring bot phrase matches (Jaro-Winkler exact $\ge 0.92$, fuzzy $\ge 0.88$), RLHF/LLM diplomatic hedging & formality markers, Flesch reading ease uniformity ($\sigma_{\text{FRE}} < 5$), comment template duplication, and generic karma-bait platitudes. |
| **$S_{\text{network}}$** | `0.20` | Co-occurrence rings (accounts repeatedly active in the same threads within 90s), repeat reciprocal reply pairs, and known-bot associations. |

### Classification Thresholds
* **`BOT`**: $\text{Score} \ge 0.65$
* **`SUSPECT`**: $0.40 \le \text{Score} < 0.65$
* **`HUMAN`**: $\text{Score} < 0.40$

---

## 🧠 Context-Aware Dynamic Weighting Engine

A static linear weighted average cannot catch modern bots. When a sophisticated bot or human astroturfer uses an LLM to generate fluent, polite, grammatically perfect English ($S_{\text{linguistic}} \approx 0.05$), static weights dilute blatant behavioral red flags ($S_{\text{temporal}} = 0.90, S_{\text{meta}} = 0.85$) down to $0.45$ (falsely marking it as HUMAN).

The **Context-Aware Dynamic Weighting Engine** solves this with intelligent behavioral adaptations:
1. **Linguistic Decoupling:** When text is clean ($S_{\text{ling}} < 0.25$) but behavioral timelines or metadata are distinctly anomalous ($\max \ge 0.50$), $w_{\text{linguistic}}$ is slashed to `0.08` while $w_{\text{temporal}}$ is boosted to `0.45` and $w_{\text{meta}}$ to `0.32`. Clean grammar can no longer shield automated behavior.
2. **Topical Monomania & Propaganda Clusters:** Accounts hyper-focused on a single community or political conflict hubs receive $+0.12$ temporal and $+0.08$ metadata boosts.
3. **High-Entropy Subreddit Scatter:** Web scrapers hopping randomly across dozens of unrelated subs receive $+0.10$ temporal velocity boost.
4. **Superhuman 24/7 Sleep Absence:** Accounts active around the clock with no natural sleep window receive $+0.10$ temporal boost.
5. **Strict Unity Normalization:** Weights are strictly re-normalized ($\sum w_i = 1.000$), preserving true probabilistic validity without score clipping or deflation.

---

## 🏛 3-Tier Evidence Hierarchy & Fast-Path Latency

To ensure browser page scans load instantly without waiting for 10-second external network lookups:

```
[Page Request] 
      │
      ├── Tier 2: Community Watchlists (<1ms O(1) in-memory lookup of 60+ verified bots/humans)
      │
      ├── Tier 1: Deterministic Ground Truth (<1ms check for standard "I am a bot" disclaimers on-page)
      │
      └── Tier 3: Probabilistic Forensics (Deep 4-layer dynamic weighting for uncataloged users)
```

### Bayesian Boost Calibration (Resolution of the "0.98 Bug")
Historically, rigid veto overrides pegged all bot scores at exactly `0.98`. This was replaced with a continuous **Bayesian boost curve**:
$$\text{Score}_{\text{final}} = \text{Score}_{\text{raw}} + (1.0 - \text{Score}_{\text{raw}}) \times \beta$$
Where decisive evidence (disclaimers $\beta=0.85$, superhuman burst $\beta=0.80$, template repetition $\beta=0.70$) smoothly elevates the score into the BOT category while preserving nuanced variance across accounts.

---

## 🛡️ Multi-Source OSINT & Hidden History Handling

When Reddit users hide their comment and post history using privacy toggles:
1. **Tri-Source Public OSINT Waterfall:** The collector queries the official Reddit API first. If history is hidden or throttled, it seamlessly falls back to **Arctic Shift** and **PullPush** public archives.
2. **Dual-Entity Recovery:** Fetches both **comments AND submissions** (titles + selftext), enabling the system to catch post-only bridge bots (like `r2tg_bot`) and link-farming spam rings.
3. **Real-Time On-Page DOM Scraping:** If a user has zero public history anywhere, the browser extension scrapes their visible on-page comment body and sends it to the API server for immediate text forensics.
4. **Dynamic Sparsity Mode:** When history is completely private and $<5$ comments are recorded, weights dynamically shift to prioritize observable evidence:
   $$\text{Meta: } 40\% \quad\Big\vert\quad \text{Linguistic: } 45\% \quad\Big\vert\quad \text{Temporal: } 5\% \quad\Big\vert\quad \text{Network: } 10\%$$

---

## 📊 Ground-Truth Benchmark Performance

The system is continuously benchmarked against a curated dataset of 15 real-world accounts across 6 operational archetypes (`botid benchmark`):

| Metric | Score | Impact |
|---|:---:|---|
| **Accuracy** | **100.0%** | 15 / 15 accounts correctly classified |
| **Precision** | **100.0%** | Zero false bot flags |
| **Recall (Sensitivity)** | **100.0%** | All automated bots successfully identified |
| **Specificity** | **100.0%** | Real humans safely identified |
| **False Positive Rate** | **0.0%** | Zero humans wrongly flagged as bots |
| **F1 Score** | **1.000** | Perfect harmonic mean |

*Catches tricky camouflaged bots that previously slipped through, including crypto tip bots without disclaimers (`tippr`) and media cross-posting bridges (`r2tg_bot`).*

---

## 🚀 Quick Start

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/Lazy-Pir8/Bot-Identifier.git
cd Bot-Identifier

# Install dependencies via requirements.txt
pip install -r requirements.txt

# Or install editable package
pip install -e .
```

### 2. Configure Environment (Optional)
The application works immediately without keys using public archives! If you wish to use official Reddit API credentials:
```bash
cp .env.example .env
# Edit .env with your REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET
```

### 3. Run Unit Tests
```bash
python -m pytest tests/ -v
# Output: 23 passed in 0.76s
```

---

## 💻 CLI Usage Guide

Run commands using `python -m src.cli` or the installed entrypoint `botid`:

### Scan a Specific User
```bash
# Scan any user (AutoModerator, RemindMeBot, or any target)
python -m src.cli scan AutoModerator

# Output raw JSON for automation / pipelines
python -m src.cli scan AutoModerator --json-out
```

### Deep Forensic Audit
```bash
# Deep-dive inspection showing rest gaps, topical monomania, and dynamic weight splits
python -m src.cli audit tippr
```

### Run Benchmark Suite
```bash
# Evaluate classifier against 15 ground-truth bots and humans
python -m src.cli benchmark
```

### Start API Server for Browser Extension
```bash
# Runs the local HTTP API on http://127.0.0.1:8000
python -m src.cli serve
```

### Start Real-Time Stream Monitoring
```bash
# Monitor all of Reddit (r/all)
python -m src.cli monitor

# Monitor specific subreddits
python -m src.cli monitor --subreddits "AskReddit,memes,technology"
```

### Inspect the Bot Database
```bash
# List all identified BOTs and SUSPECTs in local DB
python -m src.cli list-bots

# Filter by label and limit
python -m src.cli list-bots --label BOT --limit 25

# Show database summary statistics
python -m src.cli stats
```

### Add Custom Bot Phrases
```bash
python -m src.cli add-phrase "dm me for crypto signals" --type exact
python -m src.cli add-phrase "look what arrived in the mail today" --type fuzzy
```

---

## 🌐 Browser Extension

The repository includes a ready-to-load Chromium browser extension located in [`extension/`](extension/):

1. Start your local API server:
   ```bash
   python -m src.cli serve
   ```
2. Open your browser (`chrome://extensions`, `brave://extensions`, or `edge://extensions`) and turn on **Developer Mode** (top-right toggle).
3. Click **"Load unpacked"** and select the [`extension/`](extension/) directory.
4. Browse Reddit — every user handle on the screen is dynamically analyzed:
   - A single clean percentage badge (e.g. `98%`, `4%`) is injected strictly to the right of the author name.
   - Color-coded using a continuous 7-tier spectrum:
     - `0% - 14%`: Pure Emerald Green (`#10b981`)
     - `15% - 29%`: Teal / Cyan-Green (`#14b8a6`)
     - `30% - 44%`: Sky Blue (`#06b6d4`)
     - `45% - 59%`: Warm Amber / Gold (`#eab308`)
     - `60% - 74%`: Tangerine Orange (`#f97316`)
     - `75% - 85%`: Coral Crimson (`#f43f5e`)
     - `86% - 100%`: Neon Hot Red (`#ff1744`)
   - Hovering over any badge reveals a viewport-aware floating card displaying exact layer scores ($S_{\text{meta}}, S_{\text{temporal}}, S_{\text{linguistic}}, S_{\text{network}}$), active evidence tier, and detected behavioral signals without UI clipping!

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
