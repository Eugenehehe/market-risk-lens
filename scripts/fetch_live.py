"""
Fetch live market/event data and write data/latest.json.

This MVP is vendor-agnostic. It currently supports Finnhub quotes/news when
FINNHUB_API_KEY is configured in GitHub Actions secrets.

For true real-time options flow, add a provider that offers options trades,
Greeks, and unusual flow APIs. Do not put API keys in frontend JavaScript.
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
CONFIG = ROOT / "config" / "watchlist.json"


def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_finnhub_quote(symbol: str, api_key: str) -> tuple[float | None, float | None]:
    url = "https://finnhub.io/api/v1/quote"
    r = requests.get(url, params={"symbol": symbol, "token": api_key}, timeout=20)
    r.raise_for_status()
    q = r.json()
    price = q.get("c")
    prev = q.get("pc")
    change_pct = None
    if price and prev:
        change_pct = (price / prev - 1.0) * 100.0
    return price, change_pct


def get_finnhub_news(symbol: str, api_key: str) -> list[dict[str, Any]]:
    today = date.today()
    frm = today - timedelta(days=1)
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


def main() -> None:
    cfg = load_json(CONFIG, {"primary_symbol": "NVDA"})
    symbol = cfg.get("primary_symbol", "NVDA")
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    latest = load_json(DATA, {})
    now = datetime.now(timezone.utc).isoformat()

    if not finnhub_key:
        latest["generated_at"] = now
        latest["narrative"] = "尚未設定 FINNHUB_API_KEY。現在顯示 sample snapshot；設定 secret 後會自動抓 quote/news。"
        write_json(DATA, latest)
        return

    try:
        price, change_pct = get_finnhub_quote(symbol, finnhub_key)
        events = get_finnhub_news(symbol, finnhub_key)
        latest.update(
            {
                "generated_at": now,
                "snapshot": {"symbol": symbol, "price": price, "change_pct": change_pct},
                "events": events,
                "risk": risk_score(change_pct, events),
                "prepricing": latest.get("prepricing", {"score": 0, "label": "需要價格序列與事件前後資料"}),
                "options_pressure": latest.get(
                    "options_pressure",
                    {"dealer_delta_hedge_shares": 0, "label": "尚未接入 options flow API"},
                ),
                "cross_market": latest.get("cross_market", []),
                "probability_distribution": latest.get(
                    "probability_distribution",
                    {
                        "p10": None,
                        "p25": None,
                        "median": None,
                        "p75": None,
                        "p90": None,
                        "prob_up": None,
                        "prob_down_gt_1pct": None,
                        "prob_up_gt_1pct": None,
                    },
                ),
                "narrative": "Live quote/news refreshed. Options flow and probability model need a live options data provider to become fully automatic.",
            }
        )
    except Exception as exc:
        latest["generated_at"] = now
        latest["narrative"] = f"Live fetch failed: {exc}. Keeping previous snapshot."

    write_json(DATA, latest)


if __name__ == "__main__":
    main()
