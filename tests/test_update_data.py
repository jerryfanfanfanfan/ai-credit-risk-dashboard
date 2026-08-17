import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_data.py"
SPEC = importlib.util.spec_from_file_location("update_data", MODULE_PATH)
update_data = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(update_data)


def forbidden(url="https://data.sec.gov/test"):
    return HTTPError(url, 403, "Forbidden", {}, None)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"ok"


class UpdateDataTests(unittest.TestCase):
    @patch.object(update_data.time, "sleep")
    @patch.object(update_data, "urlopen")
    def test_request_text_retries_retryable_http_errors(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [forbidden(), FakeResponse()]

        result = update_data.request_text("https://example.com", attempts=2)

        self.assertEqual(result, "ok")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(1)

    @patch.object(update_data.time, "sleep")
    @patch.object(update_data, "fetch_sec_companyfacts")
    def test_sec_uses_cached_fundamentals_when_sec_is_unavailable(self, mock_fetch, _mock_sleep):
        mock_fetch.side_effect = forbidden()
        config = {"tickers": {ticker: {"cik": str(index)} for index, ticker in enumerate(("AAA", "BBB", "CCC"), 1)}}
        cached = {
            ticker: {"ocf": 10, "capex": 2, "fcf": 8, "debt": 3, "cash": 1, "net_debt": 2, "ebitda": 5, "op_income": 4, "interest": 1}
            for ticker in config["tickers"]
        }

        output, warnings, blocking, source = update_data.sec_fundamentals(config, cached)

        self.assertEqual(output, cached)
        self.assertEqual(blocking, [])
        self.assertEqual(source, "SEC cache")
        self.assertTrue(any("Retained cached SEC fundamentals" in warning for warning in warnings))

    @patch.object(update_data, "fetch_fred_series")
    def test_fred_uses_cached_public_series_when_unavailable(self, mock_fetch):
        mock_fetch.side_effect = forbidden("https://fred.stlouisfed.org/test")
        cached = {
            "source_cache": {
                "fred": {
                    name: [{"date": "2026-08-14", "value": 100 + index}]
                    for index, name in enumerate(update_data.FRED_SERIES)
                }
            }
        }

        output, warnings, blocking = update_data.fred_data(cached)

        self.assertEqual(blocking, [])
        self.assertEqual(set(output), set(update_data.FRED_SERIES))
        self.assertEqual(len(warnings), len(update_data.FRED_SERIES))
        self.assertTrue(all(rows[0]["quality"] == "cached/public" for rows in output.values()))

    @patch.object(update_data.time, "sleep")
    @patch.object(update_data, "fetch_sec_companyfacts")
    @patch.object(update_data, "fetch_fred_series")
    def test_build_dataset_can_refresh_with_cached_sec_snapshot(self, mock_fred, mock_sec, _mock_sleep):
        mock_fred.return_value = [
            {"date": "2026-08-17", "value": 100, "source": "FRED", "quality": "public"}
        ]
        mock_sec.side_effect = forbidden()
        cached = update_data.read_json(update_data.CACHE / "latest_metrics.json")

        payload, blocking = update_data.build_dataset(cached)

        self.assertEqual(blocking, [])
        self.assertEqual(payload["fundamentals"], cached["fundamentals"])
        self.assertIn("fred", payload["source_cache"])
        fundamentals = {
            metric["id"]: metric for metric in payload["metrics"]
            if metric["id"] in {"capex_to_fcf", "debt_to_capex", "net_debt_to_ebitda", "interest_coverage"}
        }
        self.assertTrue(all(metric["source"] == "SEC cache" for metric in fundamentals.values()))


if __name__ == "__main__":
    unittest.main()
