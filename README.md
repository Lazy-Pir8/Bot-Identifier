# Reddit Bot Identifier 🤖🔍

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests: Pytest](https://img.shields.io/badge/tests-pytest-green.svg)](tests/)

A real-time Reddit bot detection and monitoring engine that streams live comments, scores accounts using a multi-factor behavioral algorithm, and maintains a local database of identified bots and suspects.

> **Zero API Keys Required Out of the Box:** Automatically falls back to the open **Arctic Shift** public stream/archive if official Reddit API credentials are not provided. You can run scans and live stream monitoring immediately!

---

## 🏗 System Architecture

```
reddit-bot-identifier/
├── config/
│   ├── __init__.py
│   └── settings.py              # Environment configuration loader
├── data/
│   ├── __init__.py
│   └── bot_phrases.py           # 56 exact, 15 fuzzy & 20 regex bot patterns
├── src/
│   ├── __init__.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   └── reddit_collector.py  # Dual-engine: AsyncPRAW + Arctic Shift fallback
│   ├── features/
│   │   ├── __init__.py
│   │   ├── metadata_extractor.py  # Layer 1 (S_meta): Username regex, dormancy, karma
│   │   ├── temporal_analyzer.py   # Layer 2 (S_temporal): Circadian entropy, IAT, bursts
│   │   ├── text_forensics.py      # Layer 3 (S_linguistic): Substring phrases, LLM style
│   │   └── network_mapper.py      # Layer 4 (S_network): Co-occurrence & reply pairs
│   ├── models/
│   │   ├── __init__.py
│   │   └── bot_classifier.py    # Composite scoring + non-linear veto overrides
│   ├── db/
│   │   ├── __init__.py
│   │   └── database.py          # Async SQLite database layer
│   ├── monitor.py               # Real-time comment stream monitor & queue worker
│   └── cli.py                   # Click-based command-line interface
├── tests/
│   ├── __init__.py
│   └── test_features.py         # Full pytest test suite (16 unit tests)
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
| **$S_{\text{meta}}$** | `0.25` | Explicit bot usernames (`bot`, `automod`), auto-generated naming (`Adjective-Noun-1234`), sleeper account dormancy gaps, and karma asymmetry. |
| **$S_{\text{temporal}}$** | `0.25` | Circadian 24h Shannon entropy (lack of sleep trough), timer interval regularity ($CV < 0.15$), sliding-window burst rates, and rapid multi-subreddit hopping. |
| **$S_{\text{linguistic}}$** | `0.30` | Substring bot phrase matches, RLHF/LLM diplomatic hedging & formality markers, Flesch reading ease uniformity, and generic karma-bait platitudes. |
| **$S_{\text{network}}$** | `0.20` | Co-occurrence rings (accounts repeatedly active in the same threads within 90s), repeat reply pairs, and known-bot associations. |

### Classification Thresholds
* **`BOT`**: $\text{Score} \ge 0.65$
* **`SUSPECT`**: $0.40 \le \text{Score} < 0.65$
* **`HUMAN`**: $\text{Score} < 0.40$

### 🚨 "Smoking Gun" Non-Linear Overrides
To prevent high account age or karma from diluting obvious automated behavior, the classifier enforces three hard overrides:
1. **Automated Disclaimer**: If comments contain standard bot disclaimers (*"I am a bot, and this action was performed automatically"*), score is floored at **$\ge 0.98$**.
2. **Superhuman Velocity**: If an account posts $>15$ comments in 5 minutes across $>50\%$ rapid subreddit switches, score is floored at **$\ge 0.92$**.
3. **Explicit Bot Name**: If the username matches explicit bot naming patterns with active signals, score is floored at **$\ge 0.85$**.

---

## 🛡️ Hidden History Handling

When Reddit users hide their comment history:
1. **Dynamic Weight Redistribution**: When history is private and $<5$ comments are stored locally, weights automatically shift to:
   $$\text{Meta: } 40\% \quad\Big\vert\quad \text{Linguistic: } 45\% \quad\Big\vert\quad \text{Temporal: } 5\% \quad\Big\vert\quad \text{Network: } 10\%$$
2. **Local DB Accumulation**: Every comment streamed in real-time is saved to the local SQLite database, allowing the system to reconstruct user history locally over time.

---

## 🚀 Quick Start

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/<your-username>/reddit-bot-identifier.git
cd reddit-bot-identifier

# Install via requirements.txt
pip install -r requirements.txt

# Or install editable package
pip install -e .
```

### 2. Configure Environment (Optional)
The application works immediately without keys using Arctic Shift! If you have official Reddit API credentials:
```bash
cp .env.example .env
# Edit .env with your REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET
```

### 3. Run Unit Tests
```bash
python -m pytest tests/ -v
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

### Start Real-Time Stream Monitoring
```bash
# Monitor all of Reddit (r/all)
python -m src.cli monitor

# Monitor specific subreddits
python -m src.cli monitor --subreddits "AskReddit,memes,technology"
```

### Scan an Entire Reddit Thread / Page
```bash
# Scan all commenters in a Reddit thread URL or submission ID
python -m src.cli scan-page "https://www.reddit.com/r/technology/comments/1x8oiw0/..."
```

### Run Benchmark Suite
```bash
# Evaluate classifier against ground-truth bots and humans
python -m src.cli benchmark
```

### Start API Server for Browser Extension
```bash
# Runs the local HTTP API on http://127.0.0.1:8000
python -m src.cli serve
```

### Inspect the Bot Database
```bash
# List all identified BOTs and SUSPECTs
python -m src.cli list-bots

# Filter by label and limit
python -m src.cli list-bots --label BOT --limit 25

# Show summary statistics
python -m src.cli stats
```

### Add Custom Bot Phrases
```bash
python -m src.cli add-phrase "dm me for crypto signals" --type exact
python -m src.cli add-phrase "look what arrived in the mail today" --type fuzzy
```

---

## 🌐 Chrome / Firefox Browser Extension

The repository includes a ready-to-load browser extension located in [`extension/`](extension/):

1. Start your local server: `python -m src.cli serve`
2. Open your browser (`chrome://extensions` or `brave://extensions`) and turn on **Developer Mode**.
3. Click **"Load unpacked"** and select the `/extension` folder.
4. Browse Reddit — every user handle on the screen is dynamically analyzed with a color-coded glowing badge (Green = Human, Amber = Suspect, Neon Red = Bot) with full hover tooltips!

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
