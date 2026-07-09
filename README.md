# Market Risk Lens

Market Risk Lens is a GitHub Pages dashboard for market-event risk analysis.

It is designed to answer the questions that came up in today's trading session:

- Is the event new information, or was it already priced in?
- Did price move before the headline?
- Did options flow trigger dealer hedge pressure?
- Are cross-market signals confirming or contradicting the move?
- Is the market entering squeeze, pin-risk, risk-off, or fake-breakout mode?

## Architecture

GitHub Pages is static hosting, so this project uses:

- `index.html`, `assets/app.js`, `assets/style.css` as the frontend dashboard
- `data/latest.json` as the live snapshot read by the frontend
- GitHub Actions to refresh `data/latest.json` on a schedule
- API keys stored as GitHub Actions secrets, never in frontend JavaScript

## Important limitation

GitHub Actions scheduled workflows are near-real-time, not true tick-by-tick real-time. The practical minimum is around five minutes, and runs can be delayed. For second-level updates, use GitHub Pages only as the frontend and add a backend such as Cloudflare Workers, Vercel, Render, Supabase Edge Functions, or a local worker connected to a market-data provider.

## Setup

1. Go to repository Settings -> Pages.
2. Set Source to GitHub Actions.
3. Go to Settings -> Secrets and variables -> Actions.
4. Add secrets when available:
   - `FINNHUB_API_KEY`
   - `POLYGON_API_KEY`
   - `NEWSAPI_KEY`
5. Run the Deploy GitHub Pages workflow manually, or push to `main`.

The site should become available at:

```text
https://Eugenehehe.github.io/market-risk-lens/
```

## Local testing

```bash
python -m http.server 8000
```

Then open:

```text
http://localhost:8000
```

## Data contract

The frontend reads:

```text
data/latest.json
```

Main sections:

- `snapshot`: symbol, price, percent change
- `risk`: risk score and label
- `prepricing`: score showing whether the event may have been priced in early
- `options_pressure`: estimated dealer hedge pressure
- `events`: event timeline
- `cross_market`: NQ, WTI, US2Y, VIX, or any custom cross-market signals
- `probability_distribution`: forward-return distribution from similar events
- `narrative`: system interpretation

## Next build targets

- Real options chain and Greeks integration
- Unusual options flow provider integration
- Cross-market confirmation module
- Gamma wall / 0DTE pin-risk module
- Event-before-price anomaly detector
- Trading journal and emotional-state filter
