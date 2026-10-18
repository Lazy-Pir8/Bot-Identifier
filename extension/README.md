# Reddit Bot Identifier — Browser Extension

This Chrome/Brave/Edge/Firefox Web Extension dynamically analyzes Reddit authors as you browse and injects colored rating badges directly next to usernames in real-time.

---

## 🛠️ How to Load in Your Browser (15 seconds)

### For Chrome, Brave, Edge, Opera:
1. Open your browser and navigate to: `chrome://extensions` (or `brave://extensions` / `edge://extensions`).
2. In the top right corner, **enable "Developer mode"** (toggle switch).
3. Click the **"Load unpacked"** button in the top left.
4. Select this directory:
   ```
   /home/lazy_pirate/projects/reddit-bot-identifier/extension
   ```
5. Done! You will see the **Reddit Bot Identifier** icon in your toolbar.

---

## 🚀 How to Use It

1. **Start the local API backend**:
   ```bash
   botid serve
   # OR
   python3 -m src.server
   ```
2. **Open any Reddit page or comment section**:
   * e.g., `https://www.reddit.com/r/technology/` or any thread on `reddit.com`.
3. **Badges appear automatically**:
   * 🟢 **HUMAN** (Green): Verified normal behavior.
   * 🟡 **SUSPECT** (Amber): Unusual posting intervals or low variance.
   * 🔴 **BOT** (Neon Red / Pulsing): Superhuman burst speeds, bot phrases, or high template repetition.
4. **Hover over any badge**:
   * A dark card will pop up showing the exact score breakdown ($S_{\text{meta}}$, $S_{\text{temporal}}$, $S_{\text{linguistic}}$, $S_{\text{network}}$) and any specific signals detected!

