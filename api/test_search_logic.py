import unittest

from app import canonical_url, fuse_results, result_quality


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


if __name__ == "__main__":
    unittest.main()
