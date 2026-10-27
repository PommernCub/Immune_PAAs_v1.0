"""Configurable literature metadata searcher.

The searcher intentionally stops at metadata retrieval. It never downloads full text.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


@dataclass
class SearchHit:
    title: str
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    publication_year: int | None = None
    publication_date: str | None = None
    doi: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    publication_type: list[str] = field(default_factory=list)
    url: str | None = None


@dataclass
class SearchRecord:
    record_id: str
    title: str
    authors: list[str]
    journal: str | None
    publication_year: int | None
    publication_date: str | None
    doi: str | None
    pmid: str | None
    abstract: str | None
    publication_type: list[str]
    url: str | None
    database: str
    keyword_groups: list[str]
    search_queries: list[str]
    first_retrieved_at: str
    retrieved_at: str
    deduplication_keys: list[str]


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int, filters: dict[str, Any]) -> list[SearchHit]: ...


class PubMedProvider:
    name = "pubmed"

    def __init__(self, endpoint: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils", timeout: int = 30):
        self.endpoint, self.timeout = endpoint.rstrip("/"), timeout

    def _get(self, path: str, params: dict[str, Any]) -> bytes:
        url = f"{self.endpoint}/{path}?{urllib.parse.urlencode(params, doseq=True)}"
        request = urllib.request.Request(url, headers={"User-Agent": "Immune-PAAs-literature-searcher/0.1"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def search(self, query: str, limit: int, filters: dict[str, Any]) -> list[SearchHit]:
        query = apply_pubmed_filters(query, filters)
        root = ET.fromstring(self._get("esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "xml", "retmax": limit}))
        ids = [x.text for x in root.findall(".//Id") if x.text]
        if not ids:
            return []
        summary = ET.fromstring(self._get("esummary.fcgi", {"db": "pubmed", "id": ids, "retmode": "xml"}))
        hits: list[SearchHit] = []
        for doc in summary.findall(".//DocSum"):
            values = {item.attrib.get("Name"): (item.text or "") for item in doc.findall("Item")}
            pmid = doc.findtext("Id")
            title = values.get("Title", "").strip()
            if not title:
                continue
            year = parse_year(values.get("PubDate"))
            hits.append(SearchHit(title=title, journal=values.get("FullJournalName") or values.get("Source"),
                                  publication_year=year, publication_date=values.get("PubDate"), pmid=pmid,
                                  url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None))
        return hits


def apply_pubmed_filters(query: str, filters: dict[str, Any]) -> str:
    parts = [query]
    if filters.get("from_year") is not None:
        parts.append(f'("{int(filters["from_year"])}"[Date - Publication] : "{int(filters.get("to_year", filters["from_year"]))}"[Date - Publication])')
    elif filters.get("to_year") is not None:
        parts.append(f'("0001"[Date - Publication] : "{int(filters["to_year"])}"[Date - Publication])')
    journals = filters.get("journals") or []
    if journals:
        parts.append("(" + " OR ".join(f'"{j}"[Journal]' for j in journals) + ")")
    return " AND ".join(parts)


def parse_year(value: str | None) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", value or "")
    return int(match.group()) if match else None


def key_candidates(hit: SearchHit, rules: list[str]) -> list[str]:
    values: list[str] = []
    for rule in rules:
        if rule == "doi" and hit.doi:
            values.append("doi:" + norm_text(hit.doi).removeprefix("https://doi.org/"))
        elif rule == "pmid" and hit.pmid:
            values.append("pmid:" + norm_text(hit.pmid))
        elif rule == "title" and hit.title:
            values.append("title:" + norm_text(hit.title))
    return values or ["title:" + norm_text(hit.title)]


def make_record(hit: SearchHit, database: str, group: str, query: str, now: str, keys: list[str]) -> SearchRecord:
    record_id = keys[0].replace("/", "_")
    return SearchRecord(record_id=record_id, title=hit.title, authors=hit.authors, journal=hit.journal,
                        publication_year=hit.publication_year, publication_date=hit.publication_date, doi=hit.doi,
                        pmid=hit.pmid, abstract=hit.abstract, publication_type=hit.publication_type, url=hit.url,
                        database=database, keyword_groups=[group], search_queries=[query], first_retrieved_at=now,
                        retrieved_at=now, deduplication_keys=keys)


def search(config: dict[str, Any], providers: dict[str, SearchProvider] | None = None, clock=utc_now) -> tuple[list[SearchRecord], dict[str, Any]]:
    providers = providers or {"pubmed": PubMedProvider()}
    dedup_rules = config.get("deduplication", {}).get("keys", ["doi", "pmid", "title"])
    all_hits: list[tuple[SearchHit, str, str, str]] = []
    errors: list[dict[str, Any]] = []
    per_group = Counter(); per_db = Counter(); query_count = Counter()
    search_time = clock()
    for group in config.get("keyword_groups", []):
        name, query = group["name"], group["query"]
        for db_cfg in config.get("databases", []):
            db = db_cfg["name"]; provider = providers.get(db)
            if provider is None:
                errors.append({"keyword_group": name, "database": db, "error_type": "unsupported_database", "message": "No configured provider"})
                continue
            try:
                limit = int(group.get("result_limit", config.get("result_limit", 100)))
                hits = provider.search(query, limit, {**config.get("filters", {}), **group.get("filters", {})})
                query_count[(name, db)] += 1; per_group[name] += len(hits); per_db[db] += len(hits)
                all_hits.extend((hit, db, name, query) for hit in hits)
            except Exception as exc:  # record failures so one group does not hide other results
                errors.append({"keyword_group": name, "database": db, "error_type": type(exc).__name__, "message": str(exc)})
    merged: dict[str, SearchRecord] = {}
    for hit, db, group, query in all_hits:
        keys = key_candidates(hit, dedup_rules)
        existing = next((merged[k] for k in keys if k in merged), None)
        if existing:
            existing.keyword_groups = sorted(set(existing.keyword_groups + [group]))
            existing.search_queries = sorted(set(existing.search_queries + [query]))
            existing.deduplication_keys = sorted(set(existing.deduplication_keys + keys))
        else:
            record = make_record(hit, db, group, query, search_time, keys)
            for key in keys: merged[key] = record
    records = []
    seen_records = set()
    for record in merged.values():
        marker = id(record)
        if marker not in seen_records:
            seen_records.add(marker)
            records.append(record)
    stats: dict[str, Any] = {
        "schema_version": "literature-search-statistics/v1",
        "generated_at": clock(), "retrieved_at": search_time,
        "total_before_deduplication": len(all_hits), "total_after_deduplication": len(records),
        "keyword_group_counts": dict(per_group), "database_counts": dict(per_db),
        "query_counts": {f"{g}::{d}": n for (g, d), n in query_count.items()},
        "keyword_group_overlap_counts": overlap_counts(records),
        "year_counts": dict(Counter(str(r.publication_year) for r in records if r.publication_year)),
        "journal_counts": dict(Counter(r.journal for r in records if r.journal)),
        "publication_type_counts": dict(Counter(t for r in records for t in r.publication_type)),
        "errors": errors,
    }
    return records, stats


def overlap_counts(records: Iterable[SearchRecord]) -> dict[str, int]:
    result = Counter()
    for record in records:
        groups = sorted(record.keyword_groups)
        for i, left in enumerate(groups):
            for right in groups[i + 1:]: result[f"{left}::{right}"] += 1
    return dict(result)


def write_outputs(records: list[SearchRecord], stats: dict[str, Any], results_path: Path, stats_path: Path) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True); stats_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8") as handle:
        for record in records: handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search literature metadata without downloading full text")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--stats", required=True, type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    records, stats = search(config)
    write_outputs(records, stats, args.results, args.stats)
    print(json.dumps({"records": len(records), "errors": len(stats["errors"]), "results": str(args.results), "stats": str(args.stats)}))
    return 0 if not stats["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
