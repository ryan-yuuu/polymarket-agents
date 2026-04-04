# Polymarket Bitcoin Up/Down Markets

## What Is Polymarket?

Polymarket is a decentralized prediction market platform built on the Polygon blockchain. Users trade on the outcomes of real-world events by buying and selling shares denominated in USDC. Each market poses a binary question (e.g., "Will X happen?"), and shares are priced between $0.00 and $1.00, where the price reflects the crowd's implied probability of that outcome occurring.

When a market resolves, winning shares pay out $1.00 each and losing shares pay $0.00.

## What Are Bitcoin Up/Down Markets?

Bitcoin Up/Down markets are a family of high-frequency binary prediction markets on Polymarket. They ask a simple question: **will Bitcoin's price be higher or lower at the end of a time window compared to the start?**

Each market has exactly two outcomes:

- **Up** — BTC's price at the end of the window is greater than or equal to the price at the start.
- **Down** — BTC's price at the end of the window is strictly lower than the price at the start.

These markets run continuously across multiple timeframes and are among the highest-volume markets on the platform.

## Timeframes

There are five timeframes, each with its own resolution cadence and price source:

| Timeframe | Resolution Source | Price Pair | Schedule |
|-----------|------------------|------------|----------|
| 5 minutes | Chainlink BTC/USD oracle | BTC/USD | Every 5 min, continuous |
| 15 minutes | Chainlink BTC/USD oracle | BTC/USD | Every 15 min, continuous |
| 1 hour | Binance BTC/USDT | BTC/USDT | Every hour on the hour |
| 4 hours | Chainlink BTC/USD oracle | BTC/USD | Every 4 hours |
| Daily | Binance BTC/USDT | BTC/USDT | Noon ET to noon ET |

### Why Different Sources?

Short-duration markets (5m, 15m, 4h) use **Chainlink's decentralized oracle** for tamper-resistant, automated settlement directly on-chain. Hourly and daily markets use **Binance BTC/USDT** as the reference price, with resolution proposed through the UMA Optimistic Oracle (see [Resolution Process](#resolution-process) below).

## How Pricing Works

### Share Prices as Probabilities

Each outcome's share price represents the market's **implied probability** of that outcome. If "Up" shares are trading at $0.62, the market collectively estimates a ~62% chance that BTC will finish higher.

Because the two outcomes are mutually exclusive and exhaustive, their prices always sum to approximately $1.00 (the small gap is the spread):

```
Up price + Down price ≈ $1.00
```

For example, if Up = $0.62, then Down ≈ $0.38.

### Buying and Selling

- **Buying** shares costs the current **ask** price. You profit if the outcome wins ($1.00 payout minus your cost).
- **Selling** shares you hold returns the current **bid** price. You might sell before resolution to lock in gains or cut losses.

**Profit per winning share** = $1.00 - purchase price
**Loss per losing share** = purchase price (you lose your entire cost)

### Example Trade

You believe BTC will go up in the next hour. "Up" shares are offered at $0.55:

- You buy 200 shares for 200 x $0.55 = **$110.00**
- If BTC goes up: you receive 200 x $1.00 = $200.00, netting **$90.00 profit**
- If BTC goes down: your shares are worth $0.00, you lose **$110.00**
- Breakeven: you need BTC to go up more than 55% of the time at this price to be profitable long-term

## Resolution Rules

### Standard Rule (5m, 15m, 1h, 4h)

> "Up" wins if the closing price is **greater than or equal to** the opening price.

This means **ties favor "Up"**. If BTC's price is exactly the same at the start and end of the window, "Up" wins.

### Daily Market Rule

> "Up" wins if the Binance BTC/USDT 1-minute candle close at noon ET today is **higher than** the close at noon ET yesterday.

For daily markets, an **exact tie resolves 50-50** — both sides receive $0.50 per share. This is a different rule from the shorter timeframes.

### The "Price to Beat"

Each market displays a **"Price to Beat"** — this is the BTC price at the start of the time window. It serves as the reference point that determines whether the market resolves Up or Down.

## Resolution Process

### Chainlink-Based Markets (5m, 15m, 4h)

These resolve **automatically** via the Chainlink decentralized oracle. Settlement is near-instant once the window closes. No human intervention is required.

### Binance-Based Markets (1h, Daily)

Resolution is proposed through the **UMA Optimistic Oracle**:

1. A proposer submits the resolution with a **$750 USDC bond**.
2. A **2-hour challenge period** begins.
3. If unchallenged, the resolution is finalized.
4. If challenged, it goes to UMA's dispute resolution process where token holders vote on the correct outcome.

In practice, disputes are rare because the resolution criteria reference a publicly verifiable data source (Binance).

## Key Concepts for Trading

### Implied Probability vs. True Probability

The share price tells you what the market thinks. If you believe the **true probability** differs from the implied probability, you have an **edge**:

```
Edge = True probability - Implied probability (share price)
```

For example, if "Up" is priced at $0.48 but you estimate a 60% chance BTC goes up:
- Your edge is 0.60 - 0.48 = 0.12 (12 cents per share)
- Expected value per share = 0.60 x $1.00 - $0.48 = **$0.12**

### Expected Value (EV)

For a single share purchase at price `p` with estimated true probability `q`:

```
EV = q * (1.00 - p) - (1 - q) * p
   = q - p
```

A positive EV means the trade is profitable in expectation.

### Position Sizing

Because each share can only lose its purchase price (maximum loss is bounded), position sizing is straightforward. Common approaches:

- **Fixed percentage**: Risk a fixed % of your balance per trade (e.g., 5%).
- **Kelly criterion**: Optimal sizing based on edge and odds.
  - Kelly fraction = edge / odds = (q - p) / (1 - p), where `q` is your estimated probability and `p` is the share price.

### The Spread

The difference between the bid (sell price) and ask (buy price) is the **spread**. In active markets, the spread is typically 1-3 cents. The spread represents a cost — if you buy at $0.55 and immediately sell, you'd sell at something like $0.53, losing $0.02 per share.

### Time Decay and Market Dynamics

As a market approaches resolution:

- Prices converge toward $0.00 or $1.00 as the outcome becomes more certain.
- Liquidity may thin out in the final moments.
- Short-timeframe markets (especially 5m) are heavily influenced by **momentum** and **order flow** in the final seconds rather than fundamental analysis.

For longer timeframes (4h, daily), there's more room for analysis of trends, support/resistance levels, and macro factors.

## Infrastructure

### Blockchain Layer

- **Network**: Polygon (low gas fees, fast finality)
- **Settlement currency**: USDC (stablecoin pegged to USD)
- **Order book**: Polymarket operates a hybrid on-chain/off-chain order book using the CTF (Conditional Token Framework) exchange

### Data Sources

| Source | URL | Used For |
|--------|-----|----------|
| Chainlink BTC/USD | `data.chain.link/streams/btc-usd` | 5m, 15m, 4h resolution |
| Binance BTC/USDT | `binance.com/en/trade/BTC_USDT` | 1h, daily resolution |

### API Access

Polymarket provides:

- **REST API** (via the Gamma API) for market discovery, historical data, and resolution queries
- **WebSocket feeds** for real-time bid/ask price updates on individual tokens
- **CLOB API** for direct order book interaction

## Glossary

| Term | Definition |
|------|------------|
| **Share** | A unit of exposure to an outcome. Pays $1.00 if the outcome wins, $0.00 if it loses. |
| **Up / Down** | The two outcomes in a Bitcoin Up/Down market. |
| **Bid** | The highest price a buyer is willing to pay for a share. What you receive when selling. |
| **Ask** | The lowest price a seller is willing to accept. What you pay when buying. |
| **Mid** | The midpoint between bid and ask, often used as a fair value estimate. |
| **Implied probability** | The share price interpreted as a probability (e.g., $0.65 = 65% chance). |
| **Resolution** | The process of determining the winning outcome and paying out shares. |
| **Price to Beat** | The BTC price at the start of the time window — the reference for resolution. |
| **Token ID** | A unique identifier for each outcome's shares on the Polygon blockchain. |
| **USDC** | USD Coin, the stablecoin used for all Polymarket transactions. |
| **CTF** | Conditional Token Framework — the smart contract standard used by Polymarket for outcome tokens. |
| **UMA Oracle** | The dispute resolution system used for hourly and daily market resolution. |
| **Chainlink Oracle** | A decentralized price feed used for automated resolution of short-timeframe markets. |
