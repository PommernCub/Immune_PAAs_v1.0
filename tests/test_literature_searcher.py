import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from immune_paa.literature_searcher import SearchHit, search, write_outputs


class MockProvider:
    name = "mock"

    def search(self, query, limit, filters):
        if query == "alpha":
            return [SearchHit(title="Shared paper", doi="10.1000/SHARED", journal="J1", publication_year=2024)]
        return [
            SearchHit(title="Shared paper", doi="10.1000/shared", journal="J1", publication_year=2024),
            SearchHit(title="Only beta", doi="10.1000/BETA", journal="J2", publication_year=2023),
        ]


class LiteratureSearcherTest(unittest.TestCase):
    def test_search_deduplicates_and_preserves_provenance(self):
        config = json.loads(Path("examples/mock_search_config.json").read_text(encoding="utf-8"))
        records, stats = search(config, {"mock": MockProvider()}, clock=lambda: "2026-01-01T00:00:00Z")
        self.assertEqual(len(records), 2)
        shared = next(record for record in records if record.title == "Shared paper")
        self.assertEqual(shared.keyword_groups, ["group_a", "group_b"])
        self.assertEqual(stats["total_before_deduplication"], 3)
        self.assertEqual(stats["total_after_deduplication"], 2)
        self.assertEqual(stats["keyword_group_overlap_counts"], {"group_a::group_b": 1})
        with TemporaryDirectory() as directory:
            output = Path(directory)
            write_outputs(records, stats, output / "results.jsonl", output / "stats.json")
            self.assertEqual((output / "results.jsonl").read_text().count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
