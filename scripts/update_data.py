#!/usr/bin/env python3
"""
Build the AI Credit Risk Dashboard dataset.

The script is intentionally conservative:
- public market series come from FRED CSV endpoints where possible;
- issuer-level CDS/OAS, new-issue books, commitments and private credit data
  are loaded from transparent manual CSV templates;
- SEC companyfacts are used for reproducible fundamentals, with sample fallback.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "metrics.json"
MANUAL = ROOT / "data" / "manual"
CACHE = ROOT / "data" / "cache"
DASHBOARD_DATA = ROOT / "dashboard" / "data"

USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "ai-credit-risk-dashboard/0.1 contact: local-user@example.com",
)

RETRYABLE_HTTP_CODES = {403, 408, 429, 500, 502, 503, 504}

FRED_SERIES = {
    "ig_oas": "BAMLC0A0CM",
    "bbb_oas": "BAMLC0A4CBBB",
    "hy_oas": "BAMLH0A0HYM2",
}


SAMPLE_FRED = {
    "ig_oas": [
        ("2025-01-31", 93), ("2025-04-30", 101), ("2025-07-31", 109),
        ("2025-10-31", 119), ("2026-01-31", 132), ("2026-04-30", 148),
        ("2026-07-31", 143), ("2026-08-14", 146),
    ],
    "bbb_oas": [
        ("2025-01-31", 118), ("2025-04-30", 132), ("2025-07-31", 145),
        ("2025-10-31", 161), ("2026-01-31", 179), ("2026-04-30", 204),
        ("2026-07-31", 197), ("2026-08-14", 201),
    ],
    "hy_oas": [
        ("2025-01-31", 318), ("2025-04-30", 352), ("2025-07-31", 381),
        ("2025-10-31", 430), ("2026-01-31", 486), ("2026-04-30", 548),
        ("2026-07-31", 523), ("2026-08-14", 535),
    ],
}


def parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def request_text(url: str, timeout: int = 20, attempts: int = 3) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/csv,text/plain;q=0.9,*/*;q=0.8",
    }
    for attempt in range(attempts):
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_CODES or attempt == attempts - 1:
                raise
        except (URLError, TimeoutError, OSError):
            if attempt == attempts - 1:
                raise
        time.sleep(2**attempt)
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}")


def fetch_fred_series(series_id: str, start: str = "2018-01-01") -> list[dict[str, Any]]:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urlencode({"id": series_id, "cosd": start})
    text = request_text(url)
    rows = list(csv.DictReader(text.splitlines()))
    out = []
    for row in rows:
        raw = row.get(series_id) or row.get("VALUE") or row.get("value")
        if not raw or raw == ".":
            continue
        try:
            out.append({"date": row["observation_date"], "value": float(raw) * 100, "source": "FRED", "quality": "public"})
        except (ValueError, KeyError):
            continue
    return out


def cached_series(
    cached_payload: dict[str, Any] | None,
    metric_id: str,
    source: str,
) -> list[dict[str, Any]]:
    if not cached_payload:
        return []
    source_rows = cached_payload.get("source_cache", {}).get("fred", {}).get(metric_id)
    if source_rows is None:
        source_rows = cached_payload.get("series", {}).get(metric_id, [])
    return [
        {
            "date": row["date"],
            "value": float(row["value"]),
            "source": source,
            "quality": "cached/public",
        }
        for row in source_rows
        if row.get("date") and row.get("value") is not None
    ]


def fred_data(
    cached_payload: dict[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str], list[str]]:
    data: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    blocking_warnings: list[str] = []
    for name, series_id in FRED_SERIES.items():
        try:
            data[name] = fetch_fred_series(series_id)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            cached = cached_series(cached_payload, name, "FRED cache")
            if cached:
                warnings.append(f"FRED {series_id} unavailable, retaining cached public data: {exc}")
                data[name] = cached
            else:
                message = f"FRED {series_id} unavailable and no public cache exists: {exc}"
                warnings.append(f"{message}; using sample fallback")
                blocking_warnings.append(message)
                data[name] = [
                    {"date": d, "value": v, "source": "sample", "quality": "illustrative"}
                    for d, v in SAMPLE_FRED[name]
                ]
    return data, warnings, blocking_warnings


def fetch_sec_companyfacts(cik: str) -> dict[str, Any]:
    padded = cik.zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json"
    return json.loads(request_text(url))


def units_for_tag(facts: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    item = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not item:
        return []
    units = item.get("units", {})
    for preferred in ("USD", "shares", "pure"):
        if preferred in units:
            return units[preferred]
    for rows in units.values():
        return rows
    return []


def latest_period_values(facts: dict[str, Any], tags: list[str], form_prefix: str = "10-") -> dict[str, float]:
    values: dict[str, float] = {}
    for tag in tags:
        rows = []
        for row in units_for_tag(facts, tag):
            form = str(row.get("form", ""))
            if not form.startswith(form_prefix):
                continue
            if "val" not in row or "end" not in row:
                continue
            rows.append(row)
        rows.sort(key=lambda r: (r.get("end", ""), r.get("filed", "")))
        if rows:
            try:
                values[tag] = float(rows[-1]["val"])
            except (TypeError, ValueError):
                pass
    return values


def first_value(values: dict[str, float], tags: list[str], default: float = math.nan) -> float:
    for tag in tags:
        if tag in values and values[tag] is not None:
            return values[tag]
    return default


def sec_fundamentals(
    config: dict[str, Any],
    cached_fundamentals: dict[str, dict[str, float]] | None = None,
) -> tuple[dict[str, dict[str, float]], list[str], list[str], str]:
    warnings: list[str] = []
    blocking_warnings: list[str] = []
    output: dict[str, dict[str, float]] = {}
    failed_tickers: list[str] = []
    for index, (ticker, meta) in enumerate(config["tickers"].items()):
        if index:
            time.sleep(0.15)
        try:
            facts = fetch_sec_companyfacts(meta["cik"])
            values = latest_period_values(
                facts,
                [
                    "NetCashProvidedByUsedInOperatingActivities",
                    "PaymentsToAcquirePropertyPlantAndEquipment",
                    "PaymentsForProceedsFromProductiveAssets",
                    "CapitalExpendituresIncurredButNotYetPaid",
                    "CashAndCashEquivalentsAtCarryingValue",
                    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                    "ShortTermBorrowings",
                    "ShortTermDebt",
                    "LongTermDebt",
                    "LongTermDebtCurrent",
                    "LongTermDebtAndFinanceLeaseObligationsCurrent",
                    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
                    "OperatingIncomeLoss",
                    "DepreciationDepletionAndAmortization",
                    "DepreciationAndAmortization",
                    "InterestExpenseNonOperating",
                    "InterestExpense",
                ],
            )
            ocf = first_value(values, ["NetCashProvidedByUsedInOperatingActivities"])
            capex = abs(first_value(values, ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForProceedsFromProductiveAssets", "CapitalExpendituresIncurredButNotYetPaid"]))
            cash = first_value(values, ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], 0.0)
            debt = sum(
                x for x in [
                    first_value(values, ["ShortTermBorrowings", "ShortTermDebt"], 0.0),
                    first_value(values, ["LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent"], 0.0),
                    first_value(values, ["LongTermDebt", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"], 0.0),
                ]
                if not math.isnan(x)
            )
            op_income = first_value(values, ["OperatingIncomeLoss"])
            da = first_value(values, ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"], 0.0)
            interest = abs(first_value(values, ["InterestExpenseNonOperating", "InterestExpense"], math.nan))
            fcf = ocf - capex
            ebitda = op_income + da if not math.isnan(op_income) else math.nan
            output[ticker] = {
                "ocf": ocf,
                "capex": capex,
                "fcf": fcf,
                "debt": debt,
                "cash": cash,
                "net_debt": debt - cash,
                "ebitda": ebitda,
                "op_income": op_income,
                "interest": interest,
            }
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"SEC companyfacts unavailable for {ticker}: {exc}")
            failed_tickers.append(ticker)

    fresh_count = len(output)
    cached_used = False
    for ticker in failed_tickers:
        cached = (cached_fundamentals or {}).get(ticker)
        if cached:
            output[ticker] = cached
            cached_used = True

    if cached_used:
        warnings.append("Retained cached SEC fundamentals for unavailable issuers.")

    if len(output) < 3:
        message = "Fewer than three SEC issuers were fetched and no sufficient public cache exists."
        warnings.append(f"{message} Using sample fundamentals.")
        blocking_warnings.append(message)
        output = sample_fundamentals()
        source = "sample"
    elif fresh_count == 0 and cached_used:
        source = "SEC cache"
    elif cached_used:
        source = "SEC/cache"
    else:
        source = "SEC"
    return output, warnings, blocking_warnings, source


def sample_fundamentals() -> dict[str, dict[str, float]]:
    bn = 1_000_000_000
    return {
        "MSFT": {"ocf": 136 * bn, "capex": 77 * bn, "fcf": 59 * bn, "debt": 99 * bn, "cash": 81 * bn, "net_debt": 18 * bn, "ebitda": 168 * bn, "op_income": 144 * bn, "interest": 2.1 * bn},
        "AMZN": {"ocf": 141 * bn, "capex": 118 * bn, "fcf": 23 * bn, "debt": 178 * bn, "cash": 96 * bn, "net_debt": 82 * bn, "ebitda": 128 * bn, "op_income": 78 * bn, "interest": 4.2 * bn},
        "GOOGL": {"ocf": 125 * bn, "capex": 74 * bn, "fcf": 51 * bn, "debt": 32 * bn, "cash": 112 * bn, "net_debt": -80 * bn, "ebitda": 147 * bn, "op_income": 122 * bn, "interest": 0.8 * bn},
        "META": {"ocf": 96 * bn, "capex": 63 * bn, "fcf": 33 * bn, "debt": 49 * bn, "cash": 72 * bn, "net_debt": -23 * bn, "ebitda": 105 * bn, "op_income": 88 * bn, "interest": 1.4 * bn},
        "ORCL": {"ocf": 23 * bn, "capex": 34 * bn, "fcf": -11 * bn, "debt": 116 * bn, "cash": 12 * bn, "net_debt": 104 * bn, "ebitda": 30 * bn, "op_income": 19 * bn, "interest": 5.8 * bn},
    }


def latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=lambda r: r["date"])[-1]


def to_timeseries(rows: list[dict[str, Any]], metric_id: str, value_key: str = "value") -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if row.get(value_key) in ("", None):
            continue
        out.append(
            {
                "date": row["date"],
                "metric_id": metric_id,
                "value": float(row[value_key]),
                "source": row.get("source", "manual"),
                "quality": row.get("quality", "manual"),
                "notes": row.get("notes", ""),
            }
        )
    return out


def score_value(value: float, metric: dict[str, Any]) -> float:
    green = float(metric["green"])
    yellow = float(metric["yellow"])
    red = float(metric["red"])
    if metric["direction"] == "low_is_stress":
        if value >= green:
            return 0.0
        if value <= red:
            return 100.0
        if value >= yellow:
            return (green - value) / max(green - yellow, 1e-9) * 50.0
        return 50.0 + (yellow - value) / max(yellow - red, 1e-9) * 50.0
    if value <= green:
        return 0.0
    if value >= red:
        return 100.0
    if value <= yellow:
        return (value - green) / max(yellow - green, 1e-9) * 50.0
    return 50.0 + (value - yellow) / max(red - yellow, 1e-9) * 50.0


def status_for_score(score: float) -> str:
    if score >= 65:
        return "red"
    if score >= 35:
        return "yellow"
    return "green"


def percentile(value: float, values: list[float], direction: str) -> float:
    clean = sorted(v for v in values if not math.isnan(v))
    if not clean:
        return math.nan
    rank = sum(1 for v in clean if v <= value) / len(clean) * 100.0
    return 100.0 - rank if direction == "low_is_stress" else rank


def monthly_forward_fill(series_by_id: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    all_dates = sorted({row["date"] for rows in series_by_id.values() for row in rows})
    if not all_dates:
        return []
    output = []
    last_by_metric: dict[str, float] = {}
    for d in all_dates:
        for metric_id, rows in series_by_id.items():
            for row in rows:
                if row["date"] == d:
                    last_by_metric[metric_id] = row["value"]
        if last_by_metric:
            output.append({"date": d, **last_by_metric})
    return output


def build_dataset(
    cached_payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    config = read_json(CONFIG)
    warnings: list[str] = []
    blocking_warnings: list[str] = []

    fred, fred_warnings, fred_blocking = fred_data(cached_payload)
    warnings.extend(fred_warnings)
    blocking_warnings.extend(fred_blocking)

    series_by_id: dict[str, list[dict[str, Any]]] = {}
    series_by_id["bbb_oas"] = to_timeseries(fred["bbb_oas"], "bbb_oas")
    series_by_id["hy_oas"] = to_timeseries(fred["hy_oas"], "hy_oas")

    oracle_cds = read_csv(MANUAL / "oracle_cds.csv")
    series_by_id["oracle_5y_cds"] = to_timeseries(oracle_cds, "oracle_5y_cds")

    issuer_oas = read_csv(MANUAL / "issuer_oas.csv")
    by_date: dict[str, list[float]] = defaultdict(list)
    oracle_oas_by_date: dict[str, float] = {}
    for row in issuer_oas:
        by_date[row["date"]].append(float(row["value"]))
        if row["ticker"] == "ORCL":
            oracle_oas_by_date[row["date"]] = float(row["value"])
    series_by_id["hyperscaler_oas"] = [
        {"date": d, "metric_id": "hyperscaler_oas", "value": statistics.mean(vals), "source": "manual", "quality": "illustrative", "notes": "Average of issuer_oas.csv"}
        for d, vals in sorted(by_date.items())
    ]

    bbb_latest_by_date = {row["date"]: row["value"] for row in series_by_id["bbb_oas"]}
    for d, val in sorted(oracle_oas_by_date.items()):
        bbb_dates = [bd for bd in bbb_latest_by_date if bd <= d]
        if bbb_dates:
            bbb = bbb_latest_by_date[sorted(bbb_dates)[-1]]
            series_by_id.setdefault("oracle_vs_bbb", []).append(
                {"date": d, "metric_id": "oracle_vs_bbb", "value": val - bbb, "source": "manual+FRED", "quality": "proxy", "notes": "ORCL OAS less BBB OAS"}
            )

    ai_basket = to_timeseries(read_csv(MANUAL / "ai_bond_basket_oas.csv"), "ai_bond_oas")
    ig_by_date = {row["date"]: row["value"] for row in to_timeseries(fred["ig_oas"], "ig_oas")}
    series_by_id["ai_bond_oas_minus_ig"] = []
    for row in ai_basket:
        ig_dates = [d for d in ig_by_date if d <= row["date"]]
        if ig_dates:
            ig = ig_by_date[sorted(ig_dates)[-1]]
            series_by_id["ai_bond_oas_minus_ig"].append(
                {"date": row["date"], "metric_id": "ai_bond_oas_minus_ig", "value": row["value"] - ig, "source": "manual+FRED", "quality": "proxy", "notes": "AI basket OAS less broad IG OAS"}
            )

    new_issues = read_csv(MANUAL / "new_issues.csv")
    new_issue_dates = sorted({row["date"] for row in new_issues})
    volume_rows = []
    cover_rows = []
    concession_rows = []
    for d in new_issue_dates:
        cutoff = parse_date(d) - timedelta(days=28)
        window = [row for row in new_issues if cutoff <= parse_date(row["date"]) <= parse_date(d)]
        volume_rows.append({"date": d, "metric_id": "new_issue_volume_4w", "value": sum(float(row["amount_usd_bn"]) for row in window), "source": "manual", "quality": "illustrative", "notes": "Trailing 4-week hyperscaler issuance"})
        cover_vals = [float(row["orderbook_cover"]) for row in window if row.get("orderbook_cover")]
        concession_vals = [float(row["new_issue_concession_bp"]) for row in window if row.get("new_issue_concession_bp")]
        if cover_vals:
            cover_rows.append({"date": d, "metric_id": "orderbook_cover", "value": statistics.mean(cover_vals), "source": "manual", "quality": "illustrative", "notes": "Trailing 4-week average"})
        if concession_vals:
            concession_rows.append({"date": d, "metric_id": "new_issue_concession", "value": statistics.mean(concession_vals), "source": "manual", "quality": "illustrative", "notes": "Trailing 4-week average"})
    series_by_id["new_issue_volume_4w"] = volume_rows
    series_by_id["orderbook_cover"] = cover_rows
    series_by_id["new_issue_concession"] = concession_rows

    fundamentals, sec_warnings, sec_blocking, fundamentals_source = sec_fundamentals(
        config,
        (cached_payload or {}).get("fundamentals"),
    )
    warnings.extend(sec_warnings)
    blocking_warnings.extend(sec_blocking)
    today = date.today().isoformat()
    capex_to_fcf_vals = []
    debt_to_capex_vals = []
    nd_ebitda_vals = []
    coverage_vals = []
    for vals in fundamentals.values():
        if vals["fcf"]:
            capex_to_fcf_vals.append(vals["capex"] / vals["fcf"])
        if vals["capex"]:
            debt_to_capex_vals.append(vals["debt"] / vals["capex"])
        if vals["ebitda"]:
            nd_ebitda_vals.append(vals["net_debt"] / vals["ebitda"])
        if vals["interest"]:
            coverage_vals.append(vals["op_income"] / vals["interest"])
    fundamental_series = {
        "capex_to_fcf": statistics.median(capex_to_fcf_vals),
        "debt_to_capex": statistics.median(debt_to_capex_vals),
        "net_debt_to_ebitda": statistics.median(nd_ebitda_vals),
        "interest_coverage": statistics.median(coverage_vals),
    }
    for metric_id, value in fundamental_series.items():
        previous = cached_series(cached_payload, metric_id, "SEC cache")
        if fundamentals_source == "SEC cache" and previous:
            series_by_id[metric_id] = [
                {
                    **row,
                    "metric_id": metric_id,
                    "notes": "Previous successful SEC snapshot retained",
                }
                for row in previous
            ]
        else:
            series_by_id[metric_id] = [{
                "date": today,
                "metric_id": metric_id,
                "value": value,
                "source": fundamentals_source,
                "quality": "public/proxy" if fundamentals_source != "sample" else "illustrative",
                "notes": "Median issuer ratio",
            }]

    obs = read_csv(MANUAL / "off_balance_sheet.csv")
    by_obs_date: dict[str, list[float]] = defaultdict(list)
    for row in obs:
        commitments = float(row["purchase_commitments_usd_bn"]) + float(row["lease_obligations_usd_bn"])
        revenue = float(row["revenue_usd_bn"])
        if revenue:
            by_obs_date[row["date"]].append(commitments / revenue * 100.0)
    series_by_id["commitments_to_revenue"] = [
        {"date": d, "metric_id": "commitments_to_revenue", "value": statistics.median(vals), "source": "manual", "quality": "illustrative", "notes": "Median issuer disclosed commitments plus leases over revenue"}
        for d, vals in sorted(by_obs_date.items())
    ]

    private_rows = read_csv(MANUAL / "private_credit.csv")
    series_by_id["private_credit_proxy"] = to_timeseries(private_rows, "private_credit_proxy", "private_credit_proxy")
    series_by_id["neocloud_financing_spread"] = to_timeseries(private_rows, "neocloud_financing_spread", "neocloud_financing_spread")

    metrics = {m["id"]: m for m in config["metrics"]}
    latest_cards = []
    index_numerator = 0.0
    index_denominator = 0.0
    for metric_id, metric in metrics.items():
        rows = sorted(series_by_id.get(metric_id, []), key=lambda r: r["date"])
        if not rows:
            latest_cards.append({"id": metric_id, "name": metric["name"], "missing": True, "source": metric["public_proxy"]})
            continue
        row = rows[-1]
        values = [r["value"] for r in rows]
        score = score_value(row["value"], metric)
        hist_pct = percentile(row["value"], values, metric["direction"])
        latest_cards.append(
            {
                "id": metric_id,
                "name": metric["name"],
                "category": metric["category"],
                "unit": metric["unit"],
                "value": row["value"],
                "date": row["date"],
                "score": score,
                "status": status_for_score(score),
                "percentile": hist_pct,
                "source": row.get("source", ""),
                "quality": row.get("quality", ""),
                "frequency": metric["frequency"],
                "primary_source": metric["primary_source"],
                "public_proxy": metric["public_proxy"],
                "notes": row.get("notes") or metric.get("notes", ""),
                "meaning": metric.get("meaning", ""),
                "implication": metric.get("implication", ""),
                "thresholds": {"green": metric["green"], "yellow": metric["yellow"], "red": metric["red"], "direction": metric["direction"]},
            }
        )
        weight = float(metric.get("weight", 1))
        index_numerator += score * weight
        index_denominator += weight

    stress_index = index_numerator / index_denominator if index_denominator else math.nan

    series_compact = {
        metric_id: [
            {"date": row["date"], "value": round(row["value"], 4), "score": round(score_value(row["value"], metrics[metric_id]), 2)}
            for row in sorted(rows, key=lambda r: r["date"])
            if metric_id in metrics
        ]
        for metric_id, rows in series_by_id.items()
    }

    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "as_of": max((card.get("date", "") for card in latest_cards if not card.get("missing")), default=today),
        "stress_index": round(stress_index, 1),
        "stress_status": status_for_score(stress_index),
        "warnings": warnings,
        "metrics": latest_cards,
        "series": series_compact,
        "fundamentals": fundamentals,
        "source_cache": {"fred": fred},
        "config": config,
    }
    return payload, blocking_warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the AI credit risk dashboard dataset.")
    parser.add_argument(
        "--strict-public",
        action="store_true",
        help="Do not replace cached output when FRED or SEC public sources are unavailable.",
    )
    args = parser.parse_args()
    cached_payload = read_json(CACHE / "latest_metrics.json") if args.strict_public and (CACHE / "latest_metrics.json").exists() else None
    payload, blocking_warnings = build_dataset(cached_payload)
    if payload["warnings"]:
        print("Warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")
        if args.strict_public and blocking_warnings:
            print("Strict public mode: cached dashboard files were not changed.")
            return 1
    write_json(DASHBOARD_DATA / "metrics.json", payload)
    write_json(CACHE / "latest_metrics.json", payload)
    print(f"Wrote {DASHBOARD_DATA / 'metrics.json'}")
    print(f"AI Credit Stress Index: {payload['stress_index']} ({payload['stress_status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
