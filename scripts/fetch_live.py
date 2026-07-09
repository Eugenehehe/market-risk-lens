"""
Fetch live market/event data and write JSON snapshots for GitHub Pages.

This GitHub Pages version cannot query arbitrary symbols directly from the
browser without exposing API keys. The reliable static-site pattern is:

1. Put symbols in config/watchlist.json.
2. GitHub Actions fetches each symbol on schedule.
3. Frontend searches among generated data/symbols/<SYMBOL>.json files.

For true arbitrary real-time search, add a backend worker and keep API keys
server-side.
"""

from __future__ import annotations

import json
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


def risk_score(change_pct: float | None, events: list[dict[str, Any]]) -> dict[str, Any]:
    score = 35.0
    if change_pct is not None:
        score += min(25.0, abs(change_pct) * 8.0)
    score += min(20.0, len(events) * 2.0)
    score = int(max(0, min(100, score)))
    if score >= 75:
        label = "高風險：價格波動或事件密度偏高"
    elif score >= 50:
        label = "中等風險：需要跨市場確認"
    else:
        label = "低到中風險：暫無明顯異常"
    return {"score": score, "label": label}


def append_price_point(existing: dict[str, Any], symbol: str, price: float | None, now: str) -> list[dict[str, Any]]:
    series = existing.get("price_series", []) if isinstance(existing, dict) else []
    if price is not None:
        series.append({"timestamp": now, "close": price})
    # Deduplicate by timestamp and keep the latest 120 points.
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


def build_snapshot(symbol: str, price: float | None, change_pct: float | None, events: list[dict[str, Any]], existing: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "generated_at": now,
        "snapshot": {"symbol": symbol, "price": price, "change_pct": change_pct},
        "risk": risk_score(change_pct, events),
        "prepricing": existing.get("prepricing", {"score": 0, "label": "需要事件前後價格序列"}),
        "options_pressure": existing.get(
            "options_pressure",
            {"dealer_delta_hedge_shares": 0, "label": "尚未接入 options flow API"},
        ),
        "price_series": append_price_point(existing, symbol, price, now),
        "events": events,
        "cross_market": existing.get("cross_market", []),
        "probability_distribution": existing.get("probability_distribution", default_distribution()),
        "narrative": f"{symbol} quote/news refreshed. 目前是近即時股票/新聞層；options flow、gamma wall、pin risk 需要接入付費選擇權資料源。",
    }


def sample_snapshot(symbol: str, now: str) -> dict[str, Any]:
    existing = load_json(SYMBOL_DIR / f"{symbol}.json", {})
    return {
        "generated_at": now,
        "snapshot": {"symbol": symbol, "price": existing.get("snapshot", {}).get("price"), "change_pct": existing.get("snapshot", {}).get("change_pct")},
        "risk": {"score": 0, "label": "尚未設定 FINNHUB_API_KEY；顯示靜態資料"},
        "prepricing": existing.get("prepricing", {"score": 0, "label": "N/A"}),
        "options_pressure": existing.get("options_pressure", {"dealer_delta_hedge_shares": 0, "label": "N/A"}),
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
