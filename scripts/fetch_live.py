"""
Fetch market/event/options-chain data and write JSON snapshots for GitHub Pages.

Static-site pattern:
1. Put symbols in config/watchlist.json.
2. GitHub Actions fetches each symbol on schedule.
3. Frontend searches generated data/symbols/<SYMBOL>.json files.

Free options layer:
- Uses Yahoo Finance's public options-chain endpoint without an API key.
- Computes approximate gamma wall, put wall, call wall, max pain, and pin risk.
- This is NOT unusual-options-flow data. It does not include MLFT, active buy/sell,
  complex-order routing, dealer book, or true real-time OPRA feed.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "latest.json"
SYMBOL_DIR = ROOT / "data" / "symbols"
MANIFEST = ROOT / "data" / "manifest.json"
CONFIG = ROOT / "config" / "watchlist.json"
HEADERS = {"User-Agent": "Mozilla/5.0 MarketRiskLens/1.0"}


def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_symbol(symbol: str) -> str:
    return "".join(ch for ch in symbol.upper().strip() if ch.isalnum() or ch in {".", "-"})


def get_finnhub_quote(symbol: str, api_key: str) -> tuple[float | None, float | None]:
    url = "https://finnhub.io/api/v1/quote"
    r = requests.get(url, params={"symbol": symbol, "token": api_key}, timeout=20)
    r.raise_for_status()
    q = r.json()
    price = q.get("c")
    prev = q.get("pc")
    if price in (0, None):
        price = None
    change_pct = None
    if price and prev:
        change_pct = (price / prev - 1.0) * 100.0
    return price, change_pct


def get_finnhub_news(symbol: str, api_key: str) -> list[dict[str, Any]]:
    today = date.today()
    frm = today - timedelta(days=2)
    url = "https://finnhub.io/api/v1/company-news"
    r = requests.get(
        url,
        params={"symbol": symbol, "from": frm.isoformat(), "to": today.isoformat(), "token": api_key},
        timeout=20,
    )
    r.raise_for_status()
    items = r.json()[:8]
    events = []
    for it in items:
        ts = datetime.fromtimestamp(it.get("datetime", 0), tz=timezone.utc).isoformat()
        events.append(
            {
                "timestamp": ts,
                "title": it.get("headline", ""),
                "source": it.get("source", "Finnhub"),
                "event_type": "news",
                "severity": 3,
                "sentiment": 0,
            }
        )
    return events


def risk_score(change_pct: float | None, events: list[dict[str, Any]], options_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    score = 35.0
    if change_pct is not None:
        score += min(25.0, abs(change_pct) * 8.0)
    score += min(20.0, len(events) * 2.0)
    if options_summary:
        score += min(15.0, float(options_summary.get("pin_risk_score") or 0) * 0.15)
    score = int(max(0, min(100, score)))
    if score >= 75:
        label = "高風險：價格波動、事件或期權結構偏緊"
    elif score >= 50:
        label = "中等風險：需要跨市場與期權牆確認"
    else:
        label = "低到中風險：暫無明顯異常"
    return {"score": score, "label": label}


def append_price_point(existing: dict[str, Any], price: float | None, now: str) -> list[dict[str, Any]]:
    series = existing.get("price_series", []) if isinstance(existing, dict) else []
    if price is not None:
        series.append({"timestamp": now, "close": price})
    seen = {}
    for row in series:
        if row.get("timestamp") and row.get("close") is not None:
            seen[row["timestamp"]] = {"timestamp": row["timestamp"], "close": row["close"]}
    return list(seen.values())[-120:]


def default_distribution() -> dict[str, Any]:
    return {
        "p10": None,
        "p25": None,
        "median": None,
        "p75": None,
        "p90": None,
        "prob_up": None,
        "prob_down_gt_1pct": None,
        "prob_up_gt_1pct": None,
    }


def norm_cdf_pdf_gamma(s: float, k: float, t: float, sigma: float, r: float = 0.045) -> float:
    if s <= 0 or k <= 0 or t <= 0 or sigma <= 0:
        return 0.0
    try:
        d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
        pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
        return pdf / (s * sigma * math.sqrt(t))
    except Exception:
        return 0.0


def yahoo_options_raw(symbol: str, expiration_ts: int | None = None) -> dict[str, Any]:
    url = f"https://query2.finance.yahoo.com/v7/finance/options/{symbol}"
    params = {"date": expiration_ts} if expiration_ts else {}
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_free_options_summary(symbol: str, underlying_price: float | None) -> dict[str, Any] | None:
    try:
        first = yahoo_options_raw(symbol)
        result = (first.get("optionChain", {}).get("result") or [None])[0]
        if not result:
            return None
        expirations = result.get("expirationDates") or []
        if not expirations:
            return None
        now_ts = int(datetime.now(timezone.utc).timestamp())
        # Prefer the nearest expiration that is not already expired.
        expiry_ts = next((x for x in expirations if x >= now_ts), expirations[0])
        raw = yahoo_options_raw(symbol, expiry_ts)
        result = (raw.get("optionChain", {}).get("result") or [None])[0]
        if not result:
            return None
        quote = result.get("quote", {})
        s = underlying_price or quote.get("regularMarketPrice") or quote.get("postMarketPrice") or quote.get("preMarketPrice")
        if not s or s <= 0:
            return None
        options = (result.get("options") or [None])[0]
        if not options:
            return None
        calls = options.get("calls") or []
        puts = options.get("puts") or []
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        days = max((expiry_dt - datetime.now(timezone.utc)).total_seconds() / 86400.0, 0.25)
        t = max(days / 365.0, 1.0 / 365.0)

        by_strike: dict[float, dict[str, float]] = {}
        total_call_oi = total_put_oi = total_call_vol = total_put_vol = 0.0

        def add_rows(rows: list[dict[str, Any]], cp: str) -> None:
            nonlocal total_call_oi, total_put_oi, total_call_vol, total_put_vol
            for row in rows:
                k = row.get("strike")
                if k is None:
                    continue
                try:
                    k = float(k)
                except Exception:
                    continue
                oi = float(row.get("openInterest") or 0)
                vol = float(row.get("volume") or 0)
                iv = float(row.get("impliedVolatility") or 0)
                gamma = norm_cdf_pdf_gamma(float(s), k, t, iv)
                # Dollar gamma exposure per 1% move, common approximation.
                gex = gamma * oi * 100.0 * float(s) * float(s) * 0.01
                bucket = by_strike.setdefault(k, {"strike": k, "call_oi": 0, "put_oi": 0, "call_vol": 0, "put_vol": 0, "call_gex": 0, "put_gex": 0})
                if cp == "C":
                    bucket["call_oi"] += oi
                    bucket["call_vol"] += vol
                    bucket["call_gex"] += gex
                    total_call_oi += oi
                    total_call_vol += vol
                else:
                    bucket["put_oi"] += oi
                    bucket["put_vol"] += vol
                    bucket["put_gex"] += gex
                    total_put_oi += oi
                    total_put_vol += vol

        add_rows(calls, "C")
        add_rows(puts, "P")
        if not by_strike:
            return None

        rows = list(by_strike.values())
        for r in rows:
            r["total_oi"] = r["call_oi"] + r["put_oi"]
            r["net_gex"] = r["call_gex"] - r["put_gex"]
            r["abs_net_gex"] = abs(r["net_gex"])

        call_wall = max(rows, key=lambda x: x["call_oi"])["strike"] if total_call_oi else None
        put_wall = max(rows, key=lambda x: x["put_oi"])["strike"] if total_put_oi else None
        gamma_wall = max(rows, key=lambda x: x["abs_net_gex"])["strike"]

        # Max pain: strike where total option-holder intrinsic payout is minimized.
        candidate_strikes = sorted(by_strike.keys())
        max_pain = None
        min_payout = None
        for price in candidate_strikes:
            payout = 0.0
            for r in rows:
                payout += r["call_oi"] * max(price - r["strike"], 0.0) * 100.0
                payout += r["put_oi"] * max(r["strike"] - price, 0.0) * 100.0
            if min_payout is None or payout < min_payout:
                min_payout = payout
                max_pain = price

        near = [r for r in rows if abs(r["strike"] / float(s) - 1.0) <= 0.05]
        pin_base = near if near else rows
        pin_row = max(pin_base, key=lambda x: x["total_oi"])
        pin_strike = pin_row["strike"]
        proximity = max(0.0, 1.0 - abs(pin_strike - float(s)) / max(float(s) * 0.05, 0.01))
        concentration = pin_row["total_oi"] / max(total_call_oi + total_put_oi, 1.0)
        expiry_weight = max(0.15, min(1.0, 7.0 / max(days, 1.0)))
        pin_risk_score = int(max(0, min(100, round(100.0 * (0.55 * proximity + 0.30 * concentration + 0.15 * expiry_weight)))))

        top_strikes = sorted(rows, key=lambda x: x["total_oi"], reverse=True)[:8]
        top_strikes = [
            {
                "strike": r["strike"],
                "call_oi": int(r["call_oi"]),
                "put_oi": int(r["put_oi"]),
                "call_vol": int(r["call_vol"]),
                "put_vol": int(r["put_vol"]),
                "net_gex": round(r["net_gex"], 2),
            }
            for r in top_strikes
        ]

        return {
            "source": "Yahoo Finance public options chain, approximate Greeks/GEX",
            "status": "ok",
            "expiration": expiry_dt.date().isoformat(),
            "days_to_expiration": round(days, 2),
            "underlying_price": round(float(s), 4),
            "call_wall": call_wall,
            "put_wall": put_wall,
            "gamma_wall": gamma_wall,
            "max_pain": max_pain,
            "pin_strike": pin_strike,
            "pin_risk_score": pin_risk_score,
            "put_call_oi_ratio": round(total_put_oi / total_call_oi, 3) if total_call_oi else None,
            "total_call_oi": int(total_call_oi),
            "total_put_oi": int(total_put_oi),
            "total_call_volume": int(total_call_vol),
            "total_put_volume": int(total_put_vol),
            "net_gex_dollars_per_1pct": round(sum(r["net_gex"] for r in rows), 2),
            "top_strikes": top_strikes,
            "limitations": "No active buy/sell direction, no MLFT/complex order details, no dealer book. Use as structure map, not proof of flow.",
        }
    except Exception as exc:
        return {"source": "Yahoo Finance public options chain", "status": "failed", "error": str(exc)}


def build_snapshot(symbol: str, price: float | None, change_pct: float | None, events: list[dict[str, Any]], existing: dict[str, Any], now: str) -> dict[str, Any]:
    options_summary = fetch_free_options_summary(symbol, price)
    if options_summary and options_summary.get("status") == "ok":
        narrative = f"{symbol} quote/news refreshed. 已接入免費 Yahoo options chain，能估 call wall、put wall、gamma wall、max pain、pin risk；但仍不能取得富途那種即時大單方向、MLFT、多腿與真實 dealer book。"
    else:
        narrative = f"{symbol} quote/news refreshed. 免費 options chain 抓取失敗或暫無資料；options flow、MLFT、主動買賣方向仍需付費資料源。"
    return {
        "generated_at": now,
        "snapshot": {"symbol": symbol, "price": price, "change_pct": change_pct},
        "risk": risk_score(change_pct, events, options_summary),
        "prepricing": existing.get("prepricing", {"score": 0, "label": "需要事件前後價格序列"}),
        "options_pressure": existing.get(
            "options_pressure",
            {"dealer_delta_hedge_shares": 0, "label": "尚未接入 unusual options flow API"},
        ),
        "options_summary": options_summary,
        "price_series": append_price_point(existing, price, now),
        "events": events,
        "cross_market": existing.get("cross_market", []),
        "probability_distribution": existing.get("probability_distribution", default_distribution()),
        "narrative": narrative,
    }


def sample_snapshot(symbol: str, now: str) -> dict[str, Any]:
    existing = load_json(SYMBOL_DIR / f"{symbol}.json", {})
    return {
        "generated_at": now,
        "snapshot": {"symbol": symbol, "price": existing.get("snapshot", {}).get("price"), "change_pct": existing.get("snapshot", {}).get("change_pct")},
        "risk": {"score": 0, "label": "尚未設定 FINNHUB_API_KEY；顯示靜態資料"},
        "prepricing": existing.get("prepricing", {"score": 0, "label": "N/A"}),
        "options_pressure": existing.get("options_pressure", {"dealer_delta_hedge_shares": 0, "label": "N/A"}),
        "options_summary": existing.get("options_summary"),
        "price_series": existing.get("price_series", []),
        "events": existing.get("events", []),
        "cross_market": existing.get("cross_market", []),
        "probability_distribution": existing.get("probability_distribution", default_distribution()),
        "narrative": "尚未設定 FINNHUB_API_KEY。Settings → Secrets and variables → Actions 加入 FINNHUB_API_KEY 後，GitHub Actions 會每 5 分鐘更新 watchlist。",
    }


def main() -> None:
    cfg = load_json(CONFIG, {"primary_symbol": "NVDA", "watchlist": ["NVDA"]})
    primary = clean_symbol(cfg.get("primary_symbol", "NVDA")) or "NVDA"
    watchlist = [clean_symbol(s) for s in cfg.get("watchlist", [primary])]
    watchlist = sorted(dict.fromkeys([s for s in watchlist if s]))
    if primary not in watchlist:
        watchlist.insert(0, primary)

    finnhub_key = os.getenv("FINNHUB_API_KEY")
    now = datetime.now(timezone.utc).isoformat()
    snapshots: dict[str, dict[str, Any]] = {}

    for symbol in watchlist:
        existing = load_json(SYMBOL_DIR / f"{symbol}.json", {})
        if not finnhub_key:
            snap = sample_snapshot(symbol, now)
        else:
            try:
                price, change_pct = get_finnhub_quote(symbol, finnhub_key)
                events = get_finnhub_news(symbol, finnhub_key)
                snap = build_snapshot(symbol, price, change_pct, events, existing, now)
            except Exception as exc:
                snap = existing if existing else sample_snapshot(symbol, now)
                snap["generated_at"] = now
                snap["narrative"] = f"{symbol} live fetch failed: {exc}. Keeping previous snapshot."
        snapshots[symbol] = snap
        write_json(SYMBOL_DIR / f"{symbol}.json", snap)

    write_json(
        MANIFEST,
        {
            "generated_at": now,
            "primary_symbol": primary,
            "symbols": watchlist,
            "note": "GitHub Pages can search generated watchlist symbols. Add more symbols in config/watchlist.json.",
        },
    )
    write_json(DATA, snapshots.get(primary, sample_snapshot(primary, now)))


if __name__ == "__main__":
    main()
