# ValueBet Pilot — User Guide & Strategy Manual

A complete, non-technical operator manual for running, tuning, and monitoring the **ValueBet Pilot** automated value-betting system.

---

## Table of Contents
1. [Introduction to Value Betting](#1-introduction-to-value-betting)
2. [How the System Works](#2-how-the-system-works)
3. [Dashboard Walkthrough](#3-dashboard-walkthrough)
   - [Live Feed (Opportunities)](#live-feed)
   - [Results (Automated Settlement)](#results)
   - [Reports (P&L & Exports)](#reports)
   - [Accounts Management](#accounts-management)
   - [Settings & Strategy Configuration](#settings--strategy-configuration)
4. [Understanding Key Settings & Parameters](#4-understanding-key-settings--parameters)
   - [Pre-Match & Live Edge Thresholds](#pre-match--live-edge-thresholds)
   - [The 1.5% Re-Read Delta Rule](#the-15-re-read-delta-rule)
   - [Minimum Win Probability Filter](#minimum-win-probability-filter)
   - [Bet Markets to Scan (1X2, Over/Under, Handicap)](#bet-markets-to-scan)
   - [Safety Edge Buffer & Market Quality](#safety-edge-buffer--market-quality)
   - [Bankroll & Fractional Kelly Staking](#bankroll--fractional-kelly-staking)
5. [The Odds API & Quota Management](#5-the-odds-api--quota-management)
6. [Step-by-Step Operator Workflows](#6-step-by-step-operator-workflows)
   - [Workflow A: Setting Up a Fresh Odds API Key](#workflow-a-setting-up-a-fresh-odds-api-key)
   - [Workflow B: Daily Monitoring & Bet Tracking](#workflow-b-daily-monitoring--bet-tracking)
   - [Workflow C: Settle Pending Matches & Export Reports](#workflow-c-settle-pending-matches--export-reports)
   - [Workflow D: Cleaning Legacy Demo Data](#workflow-d-cleaning-legacy-demo-data)
7. [Recommended Settings Cheat Sheet](#7-recommended-settings-cheat-sheet)
8. [Frequently Asked Questions (FAQ)](#8-frequently-asked-questions-faq)

---

## 1. Introduction to Value Betting

### What is Positive Expected Value (+EV)?
Traditional sports bettors attempt to predict who will win a match based on opinions, news, or gut feeling. **Value betting**, in contrast, is an institutional, mathematical approach:
- Every selection in a sporting event has an underlying **true probability** of occurring.
- Bookmakers charge a commission (called the **vigorish** or **overround** / margin) by offering odds slightly below the true probability.
- When a target bookmaker (e.g. Stoiximan / Betano) is slow to adjust their prices, they occasionally offer odds that are **higher than the mathematically fair price**.
- Betting repeatedly on these overpriced selections yields a statistically proven positive return on investment (**ROI**) over the long term, regardless of the outcome of any single match.

### The Value Formula
$$\text{Expected Edge (\%)} = (\text{True Fair Win Probability} \times \text{Bookmaker Decimal Odds}) - 1$$

*Example:*
- True fair probability of Arsenal winning: **$50.0\%$** (Fair decimal odds = $2.00$).
- Target bookmaker offers: **$2.10$**.
- $\text{Edge} = (0.50 \times 2.10) - 1 = +0.05$ (**$+5.0\%$ Value**).

---

## 2. How the System Works

The ValueBet engine operates a continuous 5-step detection and settlement pipeline:

```
┌────────────────────────────────┐     ┌───────────────────────────────┐
│     Betfair Exchange (UK)      │     │         Pinnacle API          │
│   (High-volume sharp market)   │     │    (Low-margin benchmark)     │
└───────────────┬────────────────┘     └───────────────┬───────────────┘
                │                                      │
                └──────────────────┬───────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      ValueBet Math Engine                            │
│  1. De-vig sharp odds to find True Fair Probability                 │
│  2. Filter by Minimum Probability (e.g. ≥ 15%)                       │
│  3. Calculate Edge vs Target Bookmaker (Stoiximan / Betano)          │
│  4. Apply 1.5% Re-Read Delta Rule (blocks duplicate bets)            │
│  5. Size bet with Fractional Kelly Criterion                         │
└──────────────────────────────────┬───────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Live Feed & Tracking                         │
│  • Paper Trading (Simulation) or Live Mode                           │
│  • Automatic Official Score Fetching (The Odds API Scores)           │
│  • Auto-Settlement into Won / Lost / Void                            │
│  • Real-time P&L, ROI & Bankroll Analytics                           │
└──────────────────────────────────────────────────────────────────────┘
```

1. **Sharp Benchmark:** It constantly reads odds from the sharpest global betting markets: **Betfair Exchange** (where peer-to-peer liquidity establishes true market prices) and **Pinnacle** (the world's most accurate low-margin bookmaker).
2. **De-Vigging (Margin Removal):** The system strips away the bookmaker margin to derive the true, unbiased mathematical probability of each outcome.
3. **Target Price Comparison:** It scans target bookmakers (**Stoiximan / Betano**) across multiple markets (**Match Winner**, **Over/Under Totals**, **Handicap Spreads**).
4. **Value & Safety Gating:** If the target odds beat the fair price by more than your configured **Edge Threshold** (e.g. 2.0%) plus safety buffers, an alert is raised.
5. **Automatic Result Settlement:** When matches conclude, the system automatically fetches official final scores and marks bets as **Won**, **Lost**, or **Void** without requiring manual intervention.

---

## 3. Dashboard Walkthrough

Access the web interface at your server address: `http://<your-server-ip>:8000/`.

### Live Feed
The **Live Feed** is the command center displaying all newly identified value betting opportunities in real time.

![Live Feed View](https://via.placeholder.com/800x200?text=Live+Feed+Card+Preview)

- **Match Fixture:** Displays the full official event fixture (e.g. `🏟 Arsenal vs Chelsea`).
- **Actual Bet Selection & Market:** Clearly highlights the exact recommended bet (e.g. `Over 2.5 @ 2.10` or `Home Win @ 1.85`).
- **Market Types:**
  - `1X2 / Match Winner`: Home win, Draw, Away win.
  - `Over/Under Totals`: e.g. Over 2.5 goals, Under 3.5 goals.
  - `Handicap / Spreads`: Asian / European handicap lines (e.g. -1.5, +1.5).
- **Match Time:** Shows exact kickoff date and time in UTC.
- **Edge %:** The expected mathematical edge over the market.
- **Stake (€):** Recommended stake calculated via Fractional Kelly sizing.
- **Stoiximan 3-Box Odds:** Displays the bookmaker's complete market price board side-by-side with sharp reference prices.
- **Status Badges:** Shows whether a bet is in `Detected`, `Paper` (simulated), or `Placed` status.

### Results
The **Results** tab tracks all bets that have been executed or simulated.

- **Match & Actual Bet:** Shows both the match teams and the specific bet placed.
- **Match Played At:** Exact date and time when the match took place.
- **Final Score:** The official verified match score (e.g. `3 - 1`) fetched directly from sports data feeds.
- **Status (Auto-Settled):**
  - <span style="color:#10b981;font-weight:bold;">WON</span>: Bet succeeded (profit credited to bankroll).
  - <span style="color:#ef4444;font-weight:bold;">LOST</span>: Bet did not succeed.
  - <span style="color:#6366f1;font-weight:bold;">VOID</span>: Match pushed (e.g. total goals landed exactly on an integer line like Over 3.0 with a 2-1 score); stake is refunded at 0.00 P&L.
  - <span style="color:#f59e0b;font-weight:bold;">PENDING</span>: Match is currently live or scheduled in the future.
- **⚡ Auto-Sync Match Scores Now:** Click this button anytime to immediately query official match scores and settle any pending finished games.

### Reports
The **Reports** tab provides comprehensive bookkeeping, P&L analytics, and tax/accounting exports.

- **Performance Metrics:** Total bets placed, settled bets, net realized P&L (€), and Return on Investment (ROI %).
- **Auto-Calculated P&L:** Net profit is calculated automatically based on official scores and placement odds.
- **Exporting Data:** Click **Export to CSV** or **Export to Excel** to download complete records including Match Fixture, Bet Selection, Odds, Stake, Final Score, P&L, and Settlement Timestamps.

### Accounts Management
Allows configuring and tracking performance across multiple operator accounts or bankroll splits:
- Assign capital share percentages across accounts.
- Monitor deposits, withdrawals, and individual account P&L.

### Settings & Strategy Configuration
The **Settings** page allows fine-tuning the mathematical behavior of the scanner in real time. Changes made here write directly to `.env` and take effect immediately.

---

## 4. Understanding Key Settings & Parameters

### Pre-Match & Live Edge Thresholds
- **Pre-Match Edge Threshold (Recommended: `1.5% – 2.5%`):**  
  The minimum value edge required before a pre-match game is flagged.  
  *Why 2%?* In modern pre-match football markets, true mathematical value typically ranges between $1.5\%$ and $4.0\%$. Setting this too high (e.g. $5.0\%$+) will eliminate nearly all real games because 5%+ pre-match advantages are extremely rare.
- **Live (In-Play) Edge Threshold (Recommended: `3.0% – 4.0%`):**  
  Minimum value required for live, in-play matches. Live odds carry higher execution latency, so a higher margin is recommended.

### The 1.5% Re-Read Delta Rule
*How does the bot handle the same match across multiple scans?*
- Once a bet (e.g. `Arsenal vs Chelsea — Over 2.5 Goals`) is recorded, subsequent scans will check whether the market has moved.
- If the new edge is identical or changes by **less than 1.5%** (e.g. edge was $2.8\%$ and is now $3.1\%$, a change of only $0.3\%$), the scanner **skips** it.
- If the bookmaker shifts the price significantly such that the edge changes by **$\ge 1.5\%$** (e.g. from $2.8\%$ to $4.4\%$), the scanner updates the opportunity or records a fresh signal.
- **Benefit:** Completely eliminates duplicate bet spam while ensuring you capture genuine price swings.

### Minimum Win Probability Filter
- **Parameter:** `favorite_min_prob` (Recommended: `10% – 15%`)
- **What it does:** Ignores selections whose estimated fair win probability is below this number.
- **Crucial Caution:** If this setting is left at a high value (like $40\%$), the bot will **block almost all Draws, Underdogs, and Over/Under bets**, causing the feed to appear empty. Keeping it at $15\%$ (or lower) ensures full match market coverage.

### Bet Markets to Scan
Under **Settings -> Value Detection & Bet Markets**, you can toggle:
1. **Match Winner / 1X2 (`h2h`):** Traditional Home Win, Draw, Away Win.
2. **Over / Under Totals (`totals`):** Goal lines such as Over 1.5, Under 2.5, Over 3.5.
3. **Handicap Spreads (`spreads`):** Asian and European handicap lines (e.g. -1.5, +1.5).

*Note:* Each additional market enabled requests one additional quota credit per active league scan from The Odds API.

### Safety Edge Buffer & Market Quality
- **Enable Safety Edge Buffer (`enable_edge_buffer`):**  
  When **Enabled**, the engine adds an extra safety cushion (usually $+1.5\%$ to $+2.2\%$) on top of your Edge Threshold to account for bookmaker bid-ask spread and price slippage.  
  *Example:* With a $2.0\%$ Edge Threshold and Edge Buffer ON, the total required edge is approximately $3.8\%$. If you prefer strict threshold matching, you can disable this setting.
- **Pre-Match Slippage Buffer:** Extra margin (default $0.5\%$) reserved for minor price movements prior to bet placement.

### Bankroll & Fractional Kelly Staking
- **Bankroll (€):** Total capital allocated for the betting strategy (used to compute proportional bet sizing).
- **Kelly Fraction (Recommended: `25.0%` / Quarter-Kelly):**  
  The Kelly Criterion is the mathematically optimal formula for bankroll growth. Full Kelly ($100\%$) is known to produce high volatility; **Quarter-Kelly ($0.25$)** is the sports betting industry standard for steady capital growth with minimal risk of severe drawdowns.
- **Max Stake per Bet (€):** A hard monetary ceiling (e.g. €25 – €50) that no single bet can exceed, regardless of how large the value edge appears.
- **Max Total Exposure per Match (€):** Hard cap on the combined money staked across all markets on the exact same match.

---

## 5. The Odds API & Quota Management

The bot uses **The Odds API** to retrieve real-time sports fixtures, bookmaker odds, and official match scores.

### Live Quota Monitor
At the top of the **Settings** page, you will see the **Live Quota Card**:

| Badge | Meaning |
|---|---|
| <span style="color:#10b981;font-weight:bold;">● Active & Live</span> | API key is valid and has remaining request credits. |
| <span style="color:#ef4444;font-weight:bold;">● Quota Exhausted (0 Credits)</span> | All monthly requests have been used. Scanner is paused until renewed or updated. |
| <span style="color:#ef4444;font-weight:bold;">● Invalid / Expired</span> | The key is mistyped, disabled, or unauthorized. |

### Quota Consumption Guide
- **Pre-match polling interval:** 900 seconds (15 minutes). Scanning every 15 minutes provides timely price discovery while consuming only ~2,800 requests/month per sport.
- **Score Settlement:** Completed scores are cached in memory for 10 minutes to prevent unnecessary repeated requests.
- **Live Updating:** You can paste a new API key into the Settings box and click **⚡ Verify & Set Live**. The bot will immediately validate the key with the server, update `.env`, reset runtime circuit breakers, and begin scanning without restarting any containers.

---

## 6. Step-by-Step Operator Workflows

### Workflow A: Setting Up a Fresh Odds API Key
1. Go to **Settings** in the dashboard.
2. Locate the **🔑 The Odds API Key & Live Quota** panel.
3. Paste your key into the text field (e.g. `d78aa876cd16...`).
4. Click **⚡ Verify & Set Live**.
5. The status badge will instantly change to **● Active & Live**, and the remaining requests count will display your active balance.
6. A background scan will automatically trigger to populate the Live Feed with real fixtures.

### Workflow B: Daily Monitoring & Bet Tracking
1. Open the **Live Feed** tab.
2. Review new value opportunities:
   - Check the **Match Fixture** (`Team A vs Team B`).
   - Check the **Actual Bet** and **Offered Odds**.
   - Review the **Edge %** and **Stake**.
3. If running in **Paper Trading Mode** (`placement_dry_run: true`), the bot automatically simulates placements and records them in **Results**.
4. If running in **Approval Mode** (`placement_require_approval: true`), click **Approve** on bets you wish to authorize.

### Workflow C: Settle Pending Matches & Export Reports
1. Completed matches are settled automatically by the background scheduler every 15 minutes.
2. To trigger settlement immediately:
   - Navigate to **Results**.
   - Click the green **⚡ Auto-Sync Match Scores Now** button.
   - The bot fetches official final scores, checks goals/points against your selection, and records the outcome as `Won`, `Lost`, or `Void`.
3. Open **Reports**:
   - Filter by date range or sport.
   - Click **Export to Excel** or **Export to CSV** for your financial records.

### Workflow D: Cleaning Legacy Demo Data
If your database contains synthetic demo entries (such as `"Team A vs Team B"` from early test runs):
1. Go to **Settings**.
2. Scroll to the **🧹 Database Cleanup (Fake Demo Matches)** card.
3. Click **🗑 Remove Fake Demo Bets**.
4. The system will delete all mock rows without affecting any real bookmaker bets.

---

## 7. Recommended Settings Cheat Sheet

For optimal live performance on football, basketball, and tennis:

| Setting | Recommended Value | Why |
|---|---|---|
| **Pre-Match Edge Threshold** | `2.0%` (`0.02`) | Captures genuine mathematical value without demanding unrealistic pre-match margins. |
| **Live Edge Threshold** | `3.5%` (`0.035`) | Accounts for live odds volatility. |
| **Re-Read Minimum Value Change** | `1.5%` (`0.015`) | Prevents duplicate bets unless price significantly improves. |
| **Minimum Win Probability** | `15.0%` (`0.15`) | Allows normal match selections, draws, and totals while filtering extreme longshots. |
| **Minimum Bookmaker Odds** | `1.25` | Filters out low-reward heavy odds-on bets. |
| **Maximum Bookmaker Odds** | `6.00` | Excludes extreme underdogs to control bankroll variance. |
| **Kelly Fraction** | `25.0%` (`0.25`) | Industry-standard Quarter-Kelly for steady, safe compounding. |
| **Max Stake per Bet** | `€25 – €50` | Hard cap preventing excessive exposure on a single game. |
| **Pre-Match Poll Interval** | `900` seconds (15 min) | Optimal balance between fresh odds and API quota conservation. |
| **Live Poll Interval** | `300` seconds (5 min) | Conserves API quota during live matches. |
| **Bet Markets** | `h2h, totals, spreads` | Scans Match Winner, Over/Under, and Handicap lines. |
| **Paper Trading Mode** | `Enabled` during testing | Simulates all bets risk-free with automated score settlement. |
| **Allow Demo Fallback** | `Disabled` (`false`) | **Always OFF in production** to avoid synthetic test matches. |

---

## 8. Frequently Asked Questions (FAQ)

#### Q1: Why did the bot only read 4 games previously, and one showed "Team A"?
**Answer:** The previous Odds API key had consumed all 20,000 monthly requests (`requests_remaining: 0`). When API quota reaches zero and `allow_demo_fallback` is enabled, the system reverted to a built-in mock generator containing 4 simulated test fixtures (`Team A vs Team B`, Real Madrid, Bayern, PSG).  
*Resolution:* `allow_demo_fallback` is now permanently disabled, a quota exhaustion indicator is active, and fake demo entries can be cleared via the cleanup button in Settings.

#### Q2: How does the system know the match score?
**Answer:** The bot integrates with the official **The Odds API Scores Service**. Once a game finishes, the service returns verified final scores. The bot parses team scores (e.g. `3 - 1`) and evaluates whether your bet selection (`Home`, `Away`, `Over 2.5`, `Chelsea +1.5`, etc.) won, lost, or pushed.

#### Q3: What happens on a Draw or Push?
- **Match Winner:** If you bet Home or Away and the match ends in a Draw, the bet is marked **Lost** (unless betting Draw).
- **Over/Under & Handicap:** If a bet line lands on an integer (for example, Over 3.0 goals and the score is 2–1, exactly 3 goals), the bet is settled as **VOID / PUSH** and your stake is refunded with 0 profit.

#### Q4: Why don't I see new bets every single hour?
**Answer:** In professional value betting, high-margin edges are rare and episodic. Sharp bookmakers and soft bookmakers often agree on prices. When an inefficiency appears (e.g. a bookmaker is slow to react to team lineup news), the bot captures it. A quiet feed simply means the bookmakers' odds currently reflect fair market value.

#### Q5: Can I change settings without restarting the server?
**Answer:** **Yes.** All configuration changes made in the **Settings** tab update the `.env` file and live runtime memory instantly. Container restarts are not required.

---

*Document Version:* `2.0`  
*System:* `ValueBet Pilot`  
*Last Updated:* `October 2026`

