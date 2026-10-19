# Reddit Bot Identifier

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: Pytest](https://img.shields.io/badge/tests-pytest-green.svg)](tests/)

A real-time Reddit bot detection and monitoring engine. It can monitor live pages on reddit, specific users/subreddits and from their behaviour identify whether the user is a human or a bot. It creates a score on a 0-100 scale where a higher score means BOT. The score is calculated using a multi-factor behavioural algorithm along with some pre-determined constraints.

It doesn't require any API keys or credentials, as it uses Reddit API for data lookup and falls back to Third Party Sources (e.g. Arctic Shift and PullPush) which provide public stream/archive without any credentials.

---

## Documentation

Detailed architectural, mathematical, and design reports are available in the [`docs/`](docs/) directory:

- [Architecture and System Overview](docs/00_Architecture_and_System_Overview.md): Core design decisions, system components, and data flow.
- [Data Pipeline and Multi-Archive OSINT](docs/01_Data_Pipeline_and_Multi_Archive_OSINT.md): Dual-entity ingestion, public archive fallback strategies, and local caching.
- [Context-Aware Dynamic Weighting Engine](docs/02_Context_Aware_Dynamic_Weighting_Engine.md): Dynamic weight rebalancing, linguistic decoupling for LLMs, and unity normalization.
- [Four-Layer Behavioral Forensics](docs/03_Four_Layer_Behavioral_Forensics.md): Implementation details of metadata, temporal, linguistic, and network layers.
- [Three-Tier Evidence Hierarchy and Latency](docs/04_Three_Tier_Evidence_Hierarchy_and_Latency.md): Deterministic rules, watchlist caching, probabilistic scoring, and Bayesian boost calibration.
- [Browser Extension and DOM Architecture](docs/05_Browser_Extension_and_DOM_Architecture.md): Chromium extension structure, DOM observer, single-badge UI, and hover cards.
- [Benchmark Methodology and Validation](docs/06_Benchmark_Methodology_and_Validation.md): Ground-truth dataset, evaluation framework, and performance metrics across bot archetypes.

---

## System Architecture

```
reddit-bot-identifier/
├── config/
│   ├── __init__.py
│   └── settings.py              # Environment configuration loader
├── data/
│   ├── __init__.py
│   ├── bot_phrases.py           # 56 exact, 15 fuzzy, and 20 regex bot signatures
│   ├── benchmark_dataset.json   # 15 ground-truth benchmark accounts
│   └── benchmark_history.json   # Historical benchmark evaluation logs
├── docs/                        # Technical documentation suite
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
├── extension/                   # Chromium MV3 browser extension
│   ├── manifest.json
│   ├── content.js               # Page scanner, in-memory cache, and badge renderer
│   ├── styles.css               # Badge styling, color spectrum, and hover cards
│   ├── popup.html & popup.js    # Extension connection status popup
│   └── README.md
├── src/
│   ├── __init__.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   └── reddit_collector.py  # Multi-source collector (Reddit API + Arctic Shift + PullPush)
│   ├── features/
│   │   ├── __init__.py
│   │   ├── metadata_extractor.py  # Layer 1 (S_meta): Username regex, dormancy, karma ratios
│   │   ├── temporal_analyzer.py   # Layer 2 (S_temporal): Circadian entropy, intervals, burst rates
│   │   ├── text_forensics.py      # Layer 3 (S_linguistic): Bot phrases, LLM style, readability
│   │   └── network_mapper.py      # Layer 4 (S_network): Co-occurrence and reply relationships
│   ├── models/
│   │   ├── __init__.py
│   │   └── bot_classifier.py    # Dynamic Weighting Engine and composite scoring
│   ├── db/
│   │   ├── __init__.py
│   │   └── database.py          # Asynchronous SQLite storage layer
│   ├── monitor.py               # Live subreddit comment stream monitor
│   ├── server.py                # Local REST API server for extension communication
│   ├── benchmark.py             # Evaluation harness for testing classifier accuracy
│   └── cli.py                   # Command-line interface
├── tests/
│   ├── __init__.py
│   └── test_features.py         # Automated test suite (23 unit tests)
├── logs/                        # Application logs
├── .env.example                 # Configuration template
├── .gitignore                   # Ignored files and local databases
├── pyproject.toml               # Package build configuration
├── requirements.txt             # Project dependencies
├── LICENSE                      # MIT License
└── README.md
```

---

## Multi-Factor Scoring Formula

The baseline score combines four independent behavioral layers:

$$\text{Score}_{\text{bot}} = w_1 S_{\text{meta}} + w_2 S_{\text{temporal}} + w_3 S_{\text{linguistic}} + w_4 S_{\text{network}}$$

| Layer | Standard Weight | What It Evaluates |
| :--- | :---: | :--- |
| **$S_{\text{meta}}$** | `0.25` | Auto-generated username patterns (`Adjective-Noun-1234`), sleeper dormancy periods (aged accounts suddenly posting), extreme karma asymmetry, and account age. |
| **$S_{\text{temporal}}$** | `0.25` | 24-hour activity entropy, absence of human sleep windows (rest periods under 4 hours across circular midnight), timing consistency ($CV < 0.15$), burst posting, and single-subreddit concentration. |
| **$S_{\text{linguistic}}$** | `0.30` | Bot phrase matching (exact, fuzzy, and regex patterns), LLM diplomatic hedging and formality patterns, readability score uniformity, and repetitive comment templates. |
| **$S_{\text{network}}$** | `0.20` | Co-occurrence patterns across identical threads within short windows, reciprocal reply chains, and interactions with confirmed automated accounts. |

### Classification Thresholds

- **BOT**: Score >= 0.65
- **SUSPECT**: 0.40 <= Score < 0.65
- **HUMAN**: Score < 0.40

---

## Context-Aware Dynamic Weighting Engine

A static linear weighted average can fail against modern automated accounts. For example, an LLM-assisted bot generating natural, grammatically correct text will receive a very low linguistic score ($S_{\text{linguistic}} \approx 0.05$). Under static weighting, this clean text dilutes strong behavioral signals ($S_{\text{temporal}} = 0.90, S_{\text{meta}} = 0.85$) down to $0.45$, wrongly classifying the account as human.

The Context-Aware Dynamic Weighting Engine addresses this through contextual adaptations:

1. **Linguistic Decoupling**: If text appears clean ($S_{\text{linguistic}} < 0.25$) but temporal or metadata signals are elevated ($\max \ge 0.50$), $w_{\text{linguistic}}$ drops to `0.08` while $w_{\text{temporal}}$ increases to `0.45` and $w_{\text{meta}}$ to `0.32`. Clean writing alone cannot mask automated activity schedules.
2. **Topical Monomania and Propaganda Clusters**: Accounts with over 80% of their activity concentrated in a single community or politically sensitive discussions receive targeted temporal and metadata weight adjustments.
3. **High-Entropy Subreddit Dispersion**: Accounts rapidly switching across unrelated subreddits within seconds receive an increased temporal velocity weight.
4. **Continuous 24/7 Activity**: Accounts operating continuously without a natural human sleep gap receive elevated temporal weights.
5. **Strict Unity Normalization**: All weight adjustments are re-normalized ($\sum w_i = 1.000$) so the final score remains mathematically sound without arbitrary score clamping.

---

## Three-Tier Evidence Hierarchy and Latency

To provide fast responses on live web pages without waiting for redundant network requests, the system organizes evaluation into three tiers:

```
[Target Account]
       │
       ├── Tier 1: Deterministic Ground Truth (<1ms)
       │     Direct matches for official bot disclaimers on-page.
       │
       ├── Tier 2: Community Consensus & Watchlists (<1ms)
       │     In-memory cache of verified bots and recognized human accounts.
       │
       └── Tier 3: Probabilistic Forensics (Dynamic Evaluation)
             Full four-layer dynamic evaluation for uncataloged accounts.
```

### Bayesian Boost Calibration

Rather than applying rigid score overrides that set every bot to an identical value, decisive indicators use a smooth Bayesian calibration curve:

$$\text{Score}_{\text{final}} = \text{Score}_{\text{raw}} + (1.0 - \text{Score}_{\text{raw}}) \times \beta$$

Decisive signals (such as explicit disclaimers with $\beta=0.85$, superhuman burst speeds with $\beta=0.80$, or repetitive comment templates with $\beta=0.70$) cleanly move the score into the bot classification range while preserving variance reflecting the underlying evidence.

---

## Multi-Source OSINT and Hidden History Handling

When Reddit accounts hide their comment or post history:

1. **Tri-Source Public Retrieval**: The system queries the Reddit public API first. If history is hidden or throttled, it queries public archives from Arctic Shift and PullPush.
2. **Dual-Entity Analysis**: The engine collects both comments and submissions. This ensures that accounts that post submissions rather than comments (such as automated bridge bots or content syndicators) are identified.
3. **On-Page DOM Scraping**: If an account has no public archive history, the browser extension captures the visible on-page comment and passes it to the server for text forensics.
4. **Weight Redistribution for Sparse Data**: When an account's history is completely private and fewer than 5 comments exist in storage, weights adjust to reflect observable data:
   - Metadata: 40%
   - Linguistic: 45%
   - Temporal: 5%
   - Network: 10%

---

## Ground-Truth Benchmark Performance

The system is tested against a curated dataset of 15 accounts representing diverse operational profiles:

| Metric | Score | Detail |
| :--- | :---: | :--- |
| **Accuracy** | **100.0%** | 15 / 15 accounts correctly classified |
| **Precision** | **100.0%** | Zero false bot flags |
| **Recall (Sensitivity)** | **100.0%** | All automated bots identified |
| **Specificity** | **100.0%** | Human accounts accurately cleared |
| **False Positive Rate** | **0.0%** | No legitimate humans wrongly flagged |
| **F1 Score** | **1.000** | Balanced harmonic mean |

The dataset includes official utility bots, entertainment bots, crypto tip bots without disclaimers (`tippr`), automated cross-posting services (`r2tg_bot`), high-karma community figures, and regular human contributors.

---

## Quick Start

### 1. Installation

```bash
git clone https://github.com/Lazy-Pir8/Bot-Identifier.git
cd Bot-Identifier

# Install dependencies
pip install -r requirements.txt

# Or install in editable mode
pip install -e .
```

### 2. Environment Configuration (Optional)

The application functions out of the box using public archive endpoints without credentials. If you wish to use official Reddit API access:

```bash
cp .env.example .env
# Edit .env with your REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET
```

### 3. Run the Test Suite

```bash
python -m pytest tests/ -v
```

---

## Command-Line Interface

Commands can be run using `python -m src.cli` or the installed entrypoint `botid`.

### Scan an Account

```bash
# Scan any Reddit account
python -m src.cli scan AutoModerator

# Output raw JSON for automated processing
python -m src.cli scan AutoModerator --json-out
```

### Forensic Audit

```bash
# Detailed inspection showing sleep gaps, topical focus, and dynamic weights
python -m src.cli audit tippr
```

### Run Benchmark Suite

```bash
# Test the classifier against the 15 ground-truth accounts
python -m src.cli benchmark
```

### Start Local API Server

```bash
# Starts the backend server for the browser extension on http://127.0.0.1:8000
python -m src.cli serve
```

### Live Stream Monitor

```bash
# Stream and analyze comments from r/all
python -m src.cli monitor

# Stream specific subreddits
python -m src.cli monitor --subreddits "AskReddit,technology,science"
```

### Database Inspection

```bash
# List accounts stored in the local database
python -m src.cli list-bots

# Filter by classification label
python -m src.cli list-bots --label BOT --limit 25

# Show summary statistics
python -m src.cli stats
```

### Add Custom Bot Phrases

```bash
python -m src.cli add-phrase "contact me on telegram" --type exact
python -m src.cli add-phrase "check out my bio for more" --type fuzzy
```

---

## Browser Extension

The repository includes a Chromium browser extension in the [`extension/`](extension/) directory.

### Loading the Extension

1. Start the local server:
   ```bash
   python -m src.cli serve
   ```
2. Navigate to your browser's extension management page (`chrome://extensions`, `brave://extensions`, or `edge://extensions`).
3. Enable **Developer mode** in the upper right.
4. Click **Load unpacked** and select the [`extension/`](extension/) directory.

### Visual Indicators and Hover Details

- **Single Badge**: A percentage indicator is placed to the right of each username on Reddit pages.
- **Color Spectrum**:
  - `0% - 14%`: Green (`#10b981`)
  - `15% - 29%`: Teal (`#14b8a6`)
  - `30% - 44%`: Sky Blue (`#06b6d4`)
  - `45% - 59%`: Amber (`#eab308`)
  - `60% - 74%`: Orange (`#f97316`)
  - `75% - 85%`: Red-Orange (`#f43f5e`)
  - `86% - 100%`: Bright Red (`#ff1744`)
- **Hover Menu**: Hovering over a badge displays a floating details card. This card explains the exact basis of the score, including:
  - Overall verdict summary and primary driver
  - Specific behavioral observations (e.g. sleep window duration, interval regularity, subreddit concentration, template phrasing)
  - Breakdown across all four layers with active dynamic weights
- **Performance**: In-memory tab caching prevents duplicate network lookups during page scrolling, providing immediate badge rendering for previously evaluated authors.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
