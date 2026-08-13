import os
import unittest
from unittest.mock import patch

import index


class MonitorTest(unittest.TestCase):
    def test_discount_threshold_and_minimum_requests(self):
        payload = {"start": "a", "end": "b", "total_reqs": 100, "models": [
            {"model": "gpt-x", "reqs": 30, "avg_effective_provider_discount_rate": 0.1501},
            {"model": "claude-x", "reqs": 29, "avg_effective_provider_discount_rate": 0.31},
            {"model": "gpt-image-2", "reqs": 100, "avg_effective_provider_discount_rate": 0.4},
        ]}
        with patch.object(index, "_router", return_value=payload) as router:
            result = index._discount()
        self.assertTrue(result["alert"])
        self.assertTrue(result["rows"][0]["alert"])
        self.assertTrue(result["rows"][1]["observed"])
        self.assertFalse(result["rows"][2]["alert"])
        self.assertTrue(result["rows"][2]["exempt"])
        params = router.call_args.args[1]
        start = index.datetime.fromisoformat(params["start_ts"])
        end = index.datetime.fromisoformat(params["end_ts"])
        self.assertEqual(end - start, index.timedelta(minutes=30))

    def test_discount_dynamically_classifies_new_models(self):
        payload = {"start": "a", "end": "b", "total_reqs": 200, "models": [
            {"model": "gpt-9-new", "reqs": 50, "avg_effective_provider_discount_rate": 0.151},
            {"model": "codex-next", "reqs": 50, "avg_effective_provider_discount_rate": 0.151},
            {"model": "claude-future", "reqs": 50, "avg_effective_provider_discount_rate": 0.301},
            {"model": "gemini-future", "reqs": 50, "avg_effective_provider_discount_rate": 0.301},
        ]}
        with patch.object(index, "_router", return_value=payload):
            result = index._discount()
        self.assertEqual([row["model"] for row in result["rows"]], [
            "gpt-9-new", "codex-next", "claude-future", "gemini-future",
        ])
        self.assertTrue(all(row["alert"] for row in result["rows"]))

    def test_discount_uses_luna_model_threshold_without_changing_gpt_family(self):
        payload = {"start": "a", "end": "b", "total_reqs": 4724, "models": [
            {"model": "gpt-5.6-luna", "reqs": 2362, "avg_effective_provider_discount_rate": 0.3526},
            {"model": "gpt-5.6-sol", "reqs": 2362, "avg_effective_provider_discount_rate": 0.3526},
        ]}
        with patch.object(index, "_router", return_value=payload):
            result = index._discount()

        luna, sol = result["rows"]
        self.assertEqual(luna["threshold"], 0.40)
        self.assertFalse(luna["alert"])
        self.assertEqual(sol["threshold"], index.DISCOUNT_THRESHOLDS["gpt"])
        self.assertTrue(sol["alert"])

    def test_discount_matches_dashboard_strict_thresholds(self):
        payload = {"start": "a", "end": "b", "total_reqs": 90, "models": [
            {"model": "gpt-edge", "reqs": 30, "avg_effective_provider_discount_rate": 0.15},
            {"model": "claude-edge", "reqs": 30, "avg_effective_provider_discount_rate": 0.30},
            {"model": "gemini-edge", "reqs": 30, "avg_effective_provider_discount_rate": 0.30},
        ]}
        with patch.object(index, "_router", return_value=payload):
            result = index._discount()
        self.assertFalse(result["alert"])
        self.assertFalse(any(row["alert"] for row in result["rows"]))

    def test_collection_failure_is_alert(self):
        result = index._collect("x", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        self.assertTrue(result["alert"])
        self.assertIn("采集失败", result["error"])

    def test_ama_incomplete_coverage_is_only_a_note(self):
        payload = {
            "summary": {"alert_rate": 0.05, "total": 7, "total_requests": 13737},
            "metadata": {"coverage_warning": True},
        }
        with patch.object(index, "_request_json", return_value=payload), patch.dict(os.environ, {
            "AMA_BASE_URL": "http://ama",
            "AMA_API_KEY": "key",
        }):
            result = index._ama(index.datetime.now(index.timezone.utc))
        self.assertFalse(result["alert"])
        self.assertTrue(result["coverage_warning"])

    def test_success_uses_rolling_thirty_minutes(self):
        payload = {"success_rate": 99.35, "total_reqs": 208735}
        with patch.object(index, "_router", return_value=payload) as router:
            result = index._success()
        self.assertFalse(result["alert"])
        params = router.call_args.args[1]
        start = index.datetime.fromisoformat(params["start_ts"])
        end = index.datetime.fromisoformat(params["end_ts"])
        self.assertEqual(params["start"], start.strftime("%Y-%m-%d"))
        self.assertEqual(params["end"], end.strftime("%Y-%m-%d"))
        self.assertEqual(end - start, index.timedelta(minutes=30))

    def test_card_contains_production_dashboard_links(self):
        normal = {"alert": False}
        ama = {**normal, "rate": 0.01, "errors": 1, "requests": 10000}
        discount = {**normal, "rows": [], "start": "a", "end": "b", "total_requests": 10}
        success = {**normal, "rate": 99.9, "total": 10}
        card = index._card(index.datetime.now(index.timezone.utc), ama, discount, success)
        text = "\n".join(item.get("content", "") for item in card["card"]["elements"])
        self.assertIn(index.AMA_DASHBOARD_URL, text)
        self.assertIn(index.ROUTER_DISCOUNT_DASHBOARD_URL, text)
        self.assertIn(index.ROUTER_SUCCESS_DASHBOARD_URL, text)

    def test_weekday_mentions_fixed_people_and_next_hour_duty(self):
        monday_1050 = index.datetime(2026, 7, 20, 2, 50, tzinfo=index.timezone.utc)
        line = index._mention_line(monday_1050)
        self.assertIn(index.WU_YUPENG, line)
        self.assertIn(index.LI_SHUFAN, line)
        self.assertIn(index.YUAN_MING, line)
        self.assertIn("11:00", line)

    def test_weekend_duty_uses_shift_ranges(self):
        saturday_1650 = index.datetime(2026, 7, 25, 8, 50, tzinfo=index.timezone.utc)
        line = index._mention_line(saturday_1650)
        self.assertIn(index.YANG_ZIZHAO, line)
        self.assertIn("17:00", line)

    def test_duplicate_fixed_duty_mention_is_removed(self):
        monday_2150 = index.datetime(2026, 7, 20, 13, 50, tzinfo=index.timezone.utc)
        line = index._mention_line(monday_2150)
        self.assertEqual(line.count(index.WU_YUPENG), 1)

    def test_ttft_models_match_previous_monitor_batch(self):
        self.assertEqual(len(index.TTFT_MODELS), 11)
        self.assertIn("gpt-5.5", index.TTFT_MODELS)
        self.assertIn("openai-search", index.TTFT_MODELS)
        self.assertIn("gemini-3.5-flash", index.TTFT_MODELS)
        self.assertIn("gemini-3.6-flash", index.TTFT_MODELS)

    def test_ttft_format_hides_normal_models(self):
        text = index._fmt_ttft({"alert": False, "rows": [
            {"model": "gpt-5.5", "seconds": 1.234, "error": None, "alert": False},
        ]})
        self.assertNotIn("gpt-5.5", text)
        self.assertIn("全部通过", text)
        self.assertIn("阈值 < 10s", text)

    def test_ttft_format_only_shows_abnormal_models(self):
        text = index._fmt_ttft({"alert": True, "rows": [
            {"model": "normal", "seconds": 1.234, "error": None, "alert": False},
            {"model": "slow", "seconds": 10.123, "error": None, "alert": True},
        ]})
        self.assertNotIn("normal", text)
        self.assertIn("slow 10.123s", text)

    def test_image_urls_deduplicate_preview_and_download_links(self):
        text = "![图片描述](https://cdn.example.com/images/a.png) [点击下载](https://cdn.example.com/images/a.png)"
        self.assertEqual(index._image_urls(text), {"https://cdn.example.com/images/a.png"})

    def test_image_format_includes_query_and_generated_count(self):
        text = index._fmt_images({"alert": False, "rows": [{
            "label": "Nano Banana 2", "seconds": 12.345, "image_count": 2,
            "threshold": 120, "error": None, "alert": False,
        }]})
        self.assertIn("测试 query：a cat", text)
        self.assertIn("图片 2 张", text)


if __name__ == "__main__":
    unittest.main()
