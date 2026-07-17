import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_DIR = Path(__file__).parent


class FakeAPIException(Exception):
    pass


class FakeTooManyRequestsException(FakeAPIException):
    def __init__(self, suspended_time=None, message="Too many request"):
        super().__init__(message)


class FakeMainResult(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.__dict__.update(kwargs)


class FakeEngineResults(list):
    def add(self, result):
        self.append(result)


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.text)


def load_engine(name):
    searx = types.ModuleType("searx")
    exceptions = types.ModuleType("searx.exceptions")
    exceptions.SearxEngineAPIException = FakeAPIException
    exceptions.SearxEngineTooManyRequestsException = FakeTooManyRequestsException
    network = types.ModuleType("searx.network")
    network.post = lambda *_args, **_kwargs: None
    result_types = types.ModuleType("searx.result_types")
    result_types.EngineResults = FakeEngineResults
    result_types.MainResult = FakeMainResult

    old_modules = {
        key: sys.modules.get(key)
        for key in ("searx", "searx.exceptions", "searx.network", "searx.result_types")
    }
    sys.modules.update({
        "searx": searx,
        "searx.exceptions": exceptions,
        "searx.network": network,
        "searx.result_types": result_types,
    })
    try:
        spec = importlib.util.spec_from_file_location(f"test_{name}_engine", ENGINE_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in old_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class ZaiMcpEngineTests(unittest.TestCase):
    def test_search_uses_mcp_session_and_returns_normalized_results(self):
        engine = load_engine("zai")
        engine._secret = lambda: "test-zai-key"
        initialize = FakeResponse(
            text='event:message\ndata:{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}\n\n',
            headers={"Mcp-Session-Id": "session-123", "Content-Type": "text/event-stream"},
        )
        initialized = FakeResponse(status_code=202, text="", headers={})
        mcp_items = [{
            "title": "MCP specification",
            "link": "https://modelcontextprotocol.io/specification",
            "content": "The official protocol specification.",
            "refer": "ref_1",
        }]
        tool_rpc = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": json.dumps(json.dumps(mcp_items))}],
                "isError": False,
            },
        }
        tool_response = FakeResponse(
            text=f"event:message\ndata:{json.dumps(tool_rpc, separators=(',', ':'))}\n\n",
            headers={"Content-Type": "text/event-stream"},
        )

        params = {
            "headers": {}, "pageno": 1, "time_range": "month",
            "searxng_locale": "en-US", "json": {},
        }
        with patch.object(engine, "post", side_effect=[initialize, initialized]) as post:
            engine.request("MCP transport", params)

        self.assertEqual(post.call_count, 2)
        self.assertEqual(params["url"], "https://api.z.ai/api/mcp/web_search_prime/mcp")
        self.assertEqual(params["headers"]["Mcp-Session-Id"], "session-123")
        self.assertEqual(params["headers"]["MCP-Protocol-Version"], "2024-11-05")
        self.assertEqual(params["json"]["method"], "tools/call")
        self.assertEqual(params["json"]["params"]["name"], "web_search_prime")
        self.assertEqual(params["json"]["params"]["arguments"], {
            "search_query": "MCP transport",
            "search_recency_filter": "oneMonth",
            "content_size": "medium",
            "location": "us",
        })

        results = engine.response(tool_response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://modelcontextprotocol.io/specification")
        self.assertEqual(results[0].title, "MCP specification")
        self.assertEqual(results[0].content, "The official protocol specification.")

    def test_malformed_mcp_response_is_reported_as_provider_failure(self):
        engine = load_engine("zai")
        malformed = FakeResponse(
            text="event:message\ndata:not-json\n\n",
            headers={"Content-Type": "text/event-stream"},
        )

        with self.assertRaisesRegex(FakeAPIException, "malformed"):
            engine.response(malformed)


class SerperEngineTests(unittest.TestCase):
    def test_engine_rejects_startup_without_api_key(self):
        engine = load_engine("serper")
        engine._secret = lambda: ""

        with self.assertRaisesRegex(FakeAPIException, "missing"):
            engine.init({})

    def test_search_builds_serper_request_and_returns_organic_results(self):
        engine = load_engine("serper")
        engine._secret = lambda: "test-serper-key"
        params = {
            "headers": {}, "pageno": 2, "time_range": "week",
            "searxng_locale": "en-US", "json": {},
        }

        engine.request("MCP documentation", params)

        self.assertEqual(params["url"], "https://google.serper.dev/search")
        self.assertEqual(params["headers"]["X-API-KEY"], "test-serper-key")
        self.assertEqual(params["json"], {
            "q": "MCP documentation", "num": 10, "page": 2,
            "hl": "en", "tbs": "qdr:w",
        })

        response = FakeResponse(payload={"organic": [{
            "title": "Model Context Protocol",
            "link": "https://modelcontextprotocol.io/",
            "snippet": "The official MCP documentation.",
        }]})
        results = engine.response(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://modelcontextprotocol.io/")
        self.assertEqual(results[0].content, "The official MCP documentation.")

    def test_rate_limit_is_classified_without_leaking_response_details(self):
        engine = load_engine("serper")

        with self.assertRaisesRegex(FakeTooManyRequestsException, "Serper rate limit"):
            engine.response(FakeResponse(status_code=429, payload={"secret": "must-not-leak"}))


class EngineListTests(unittest.TestCase):
    def test_provider_engines_are_listed_once_and_not_as_google_cses(self):
        spec = importlib.util.spec_from_file_location(
            "list_engines", ENGINE_DIR.parent.parent / "scripts" / "list_engines.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        rows = module.engine_rows({
            "use_default_settings": {"engines": {"keep_only": ["zai", "bing"]}},
            "engines": [
                {"name": "zai", "engine": "zai"},
                {"name": "serper", "engine": "serper"},
                {"name": "cse docs", "engine": "google_cse"},
            ],
        })

        self.assertEqual(rows, [
            ("zai", "provider"),
            ("bing", "built-in"),
            ("serper", "provider"),
            ("cse docs", "Google CSE"),
        ])


if __name__ == "__main__":
    unittest.main()
