#!/usr/bin/env python3
"""
Web research helper — fetches real facts, statistics, and quotes with citations
for script generation. Uses Google Custom Search API (free tier: 100 queries/day)
or falls back to DuckDuckGo HTML scraping (no key needed).

Usage:
    python helpers/web_research.py --topic "Why is Mongolia so empty" --output research.json
    python helpers/web_research.py --queries "Mongolia population density,Mongolia nomadic culture" --output research.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from dotenv import load_dotenv

load_dotenv()


class WebResearcher:
    """Multi-source web researcher that extracts facts with citations."""

    def __init__(self):
        self.google_api_key = os.getenv("GOOGLE_SEARCH_API_KEY", "")
        self.google_cx = os.getenv("GOOGLE_SEARCH_CX", "")

    # ── Google Custom Search API ─────────────────────────────────

    def search_google(self, query: str, num_results: int = 10) -> list[dict]:
        """Google Custom Search JSON API. Returns snippets + URLs + titles.
        Free: 100 queries/day. Set GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX."""
        if not self.google_api_key or not self.google_cx:
            return []
        try:
            resp = httpx.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": self.google_api_key,
                    "cx": self.google_cx,
                    "q": query,
                    "num": min(num_results, 10),
                },
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            results = []
            for item in items:
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "url": item.get("link", ""),
                    "source": item.get("displayLink", ""),
                    "date": item.get("pagemap", {}).get("metatags", [{}])[0].get(
                        "article:published_time", ""
                    ) if item.get("pagemap") else "",
                })
            return results
        except Exception as e:
            print(f"[web_research] Google search failed: {e}", file=sys.stderr)
            return []

    # ── DuckDuckGo HTML fallback (no API key) ────────────────────

    def search_duckduckgo(self, query: str, max_results: int = 8) -> list[dict]:
        """Scrape DuckDuckGo HTML lite for snippets. No API key needed."""
        try:
            resp = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=15,
                follow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text
            results = []
            # Parse result blocks
            blocks = re.findall(
                r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'<a class="result__snippet"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            )
            for url, title, snippet in blocks[:max_results]:
                title = re.sub(r"<[^>]+>", "", title).strip()
                snippet = re.sub(r"<[^>]+>", "", snippet).strip()
                source = re.sub(r"https?://(?:www\.)?", "", url).split("/")[0]
                results.append({
                    "title": title,
                    "snippet": snippet,
                    "url": url,
                    "source": source,
                    "date": "",
                })
            return results
        except Exception as e:
            print(f"[web_research] DuckDuckGo search failed: {e}", file=sys.stderr)
            return []

    # ── Wikipedia quick facts ────────────────────────────────────

    def search_wikipedia(self, topic: str, sentences: int = 10) -> dict:
        """Fetch Wikipedia summary — high-quality baseline facts."""
        try:
            resp = httpx.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote_plus(topic),
                headers={"User-Agent": "video-agent-research/1.0"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "title": data.get("title", ""),
                    "summary": data.get("extract", ""),
                    "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    "source": "wikipedia.org",
                }
        except Exception:
            pass
        return {}

    # ── Multi-query research ─────────────────────────────────────

    def deep_research(self, topic: str, queries: Optional[list[str]] = None) -> dict:
        """Run multiple search queries + Wikipedia to build a fact base.
        Returns structured research data for LLM consumption."""
        if queries is None:
            # Auto-generate research queries from topic
            queries = self._generate_research_queries(topic)

        all_results = []
        seen_urls = set()

        # Wikipedia first — reliable baseline
        wiki = self.search_wikipedia(topic)
        if wiki and wiki.get("summary"):
            all_results.append({
                "query": f"wikipedia: {topic}",
                "results": [wiki],
            })

        # Search each query
        for q in queries:
            if self.google_api_key and self.google_cx:
                results = self.search_google(q, num_results=5)
            else:
                results = self.search_duckduckgo(q, max_results=5)

            # Deduplicate by URL
            deduped = []
            for r in results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    deduped.append(r)
            if deduped:
                all_results.append({"query": q, "results": deduped})

        report = {
            "topic": topic,
            "researched_at": datetime.utcnow().isoformat() + "Z",
            "queries_used": queries,
            "total_sources": sum(len(r["results"]) for r in all_results),
            "search_results": all_results,
        }
        print(
            f"[web_research] {len(queries)} queries → {report['total_sources']} sources",
            file=sys.stderr,
        )
        return report

    def _generate_research_queries(self, topic: str) -> list[str]:
        """Generate 6-10 research queries from a topic to cover different angles."""
        base = topic.strip()
        return [
            f"{base} statistics data",
            f"{base} facts",
            f"{base} history origin",
            f"{base} expert opinion research study",
            f"{base} surprising interesting why",
            f"{base} comparison versus",
            f'"{base}" according to report',
            f"{base} 2024 2025 latest",
        ]


def main():
    parser = argparse.ArgumentParser(description="Web research for video scripts")
    parser.add_argument("--topic", help="Topic to research")
    parser.add_argument(
        "--queries", help="Comma-separated search queries (overrides auto-generation)"
    )
    parser.add_argument("--output", help="Output JSON file path")
    args = parser.parse_args()

    if not args.topic and not args.queries:
        print("ERROR: --topic or --queries required", file=sys.stderr)
        sys.exit(1)

    researcher = WebResearcher()
    queries = args.queries.split(",") if args.queries else None
    topic = args.topic or (queries[0] if queries else "")

    report = researcher.deep_research(topic, queries)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[web_research] saved to {args.output}", file=sys.stderr)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
