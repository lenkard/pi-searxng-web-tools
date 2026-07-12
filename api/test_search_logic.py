import ipaddress
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

import app
from app import SearchBody, canonical_url, do_search, fuse_results, result_quality


class SearchLogicTests(unittest.TestCase):
    def test_canonical_url_removes_tracking_and_fragment(self):
        self.assertEqual(
            canonical_url("https://Example.com/docs/?utm_source=test&x=1#part"),
            "https://example.com/docs?x=1",
        )

    def test_relevant_diverse_results_score_higher(self):
        relevant = [
            {
                "title": "FastAPI lifespan documentation",
                "url": f"https://docs{i}.example/fastapi",
                "content": "Python FastAPI lifespan context manager tutorial",
            }
            for i in range(5)
        ]
        irrelevant = [
            {"title": "Unrelated page", "url": "https://example.com/a", "content": "shopping and travel"}
        ]
        self.assertGreater(result_quality("FastAPI lifespan documentation", relevant), 0.8)
        self.assertLess(result_quality("FastAPI lifespan documentation", irrelevant), 0.3)

    def test_fusion_deduplicates_and_tracks_engines(self):
        lists = [
            [{"title": "Docs", "url": "https://example.com/docs?utm_source=a", "engine": "one", "content": "short"}],
            [{"title": "Docs", "url": "https://example.com/docs", "engine": "two", "content": "a richer description"}],
        ]
        result = fuse_results(lists, 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_engines"], ["one", "two"])
        self.assertEqual(result[0]["content"], "a richer description")

    def test_ssrf_blocks_non_global_ipv4_and_ipv6(self):
        for address in ("0.0.0.0", "127.0.0.1", "169.254.169.254", "192.0.2.1", "::", "::1", "2001:db8::1", "ff02::1"):
            self.assertTrue(app._is_blocked_ip(ipaddress.ip_address(address)), address)
        for address in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"):
            self.assertFalse(app._is_blocked_ip(ipaddress.ip_address(address)), address)

    def test_rate_limit_expires_without_becoming_broken(self):
        app.ENGINE_HEALTH.clear()
        for _ in range(app.ENGINE_BROKEN_THRESHOLD + 1):
            app._record_probe("cse reddit", True, "HTTP 429 too many requests", None)
        self.assertEqual(app.ENGINE_HEALTH["cse reddit"]["status"], "rate_limited")
        self.assertEqual(app.ENGINE_HEALTH["cse reddit"]["consecutive_failures"], 0)
        self.assertIn("google cse", app.ENGINE_COOLDOWNS)
        app.ENGINE_HEALTH["cse reddit"]["retry_after"] = time.time() - 1
        self.assertEqual(app._effective_status("cse reddit"), "stale")
        app.ENGINE_HEALTH.clear()
        app.ENGINE_COOLDOWNS.clear()

    def test_persisted_cse_rate_limit_restores_shared_cooldown(self):
        app.ENGINE_HEALTH.clear()
        app.ENGINE_COOLDOWNS.clear()
        app.ENGINE_HEALTH["cse reddit"] = {
            "status": "rate_limited", "retry_after": time.time() + 300,
            "last_error": "too many requests",
        }
        app._restore_cse_cooldown()
        self.assertTrue(app._is_temporarily_unavailable("cse documents"))
        app.ENGINE_HEALTH.clear()
        app.ENGINE_COOLDOWNS.clear()

    def test_confirmed_failure_recovers_as_a_runtime_canary_after_cooldown(self):
        app.ENGINE_HEALTH.clear()
        app._record_probe("reddit", True, "access denied", None)
        self.assertEqual(app._effective_status("reddit"), "degraded")
        app._record_probe("reddit", True, "Suspended: access denied", None)
        self.assertEqual(app._effective_status("reddit"), "broken")
        self.assertTrue(app._is_temporarily_unavailable("reddit"))
        app.ENGINE_HEALTH["reddit"]["retry_after"] = time.time() - 1
        self.assertEqual(app._effective_status("reddit"), "stale")
        self.assertFalse(app._is_temporarily_unavailable("reddit"))
        app.ENGINE_HEALTH.clear()


class AutoRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_cses_are_rejected_before_upstream_request(self):
        request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
        with patch.object(app, "ALLOWED_ENGINES", {"google cse", "cse reddit"}):
            with self.assertRaisesRegex(HTTPException, "At most one Google CSE"):
                await do_search(SearchBody(q="test", engines="google cse,cse reddit"), request)

    async def test_single_cse_is_globally_paced(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"query": "test", "results": [], "unresponsive_engines": []}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def get(self, _url, params): return Response()

        request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
        pace = AsyncMock()
        app.SEARCH_CACHE.clear()
        with (
            patch.object(app, "ALLOWED_ENGINES", {"cse reddit"}),
            patch.object(app, "_pace_cse_request", pace),
            patch.object(app, "save_health"),
            patch.object(app.httpx, "AsyncClient", return_value=Client()),
        ):
            await do_search(SearchBody(q="test", engines="cse reddit", mode="fast"), request)
        pace.assert_awaited_once()

    async def test_runtime_failure_schedules_one_confirmation(self):
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"query": "test", "results": [], "unresponsive_engines": [["bing", "access denied"]]}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def get(self, _url, params): return Response()

        app.ENGINE_HEALTH.clear()
        app.ENGINE_COOLDOWNS.clear()
        app.SEARCH_CACHE.clear()
        request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
        with (
            patch.object(app, "save_health"),
            patch.object(app, "_schedule_confirmation") as schedule,
            patch.object(app.httpx, "AsyncClient", return_value=Client()),
        ):
            await do_search(SearchBody(q="test", engines="bing", mode="fast"), request)
        schedule.assert_called_once_with("bing")
        self.assertEqual(app.ENGINE_HEALTH["bing"]["status"], "degraded")
        app.ENGINE_HEALTH.clear()

    async def test_auto_falls_back_when_routed_cse_is_rate_limited(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "query": "best laptop reddit opinions",
                    "results": [{
                        "title": "Laptop guide", "url": "https://example.com/laptop",
                        "content": "best laptop guide", "engine": "bing", "score": 1,
                    }],
                    "unresponsive_engines": [],
                }

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, _url, params):
                self.params = params
                return Response()

        app.ENGINE_HEALTH.clear()
        app.ENGINE_COOLDOWNS.clear()
        app.SEARCH_CACHE.clear()
        app.ENGINE_HEALTH["cse reddit"] = {
            "status": "rate_limited", "retry_after": time.time() + 300,
        }
        request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
        with (
            patch.object(app, "SEARXNG_DEFAULT_ENGINES", "bing"),
            patch.object(app, "save_health"),
            patch.object(app.httpx, "AsyncClient", return_value=Client()),
        ):
            result = await do_search(SearchBody(q="best laptop reddit opinions", engines="auto", mode="fast"), request)

        self.assertEqual(result["attempted_engines"], ["bing"])
        self.assertEqual(result["excluded_unavailable"], ["cse reddit"])
        self.assertEqual(len(result["results"]), 1)
        app.ENGINE_HEALTH.clear()


if __name__ == "__main__":
    unittest.main()
