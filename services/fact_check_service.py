"""Fact Check Service — External verification APIs.

Provides:
- Wikipedia API for general knowledge verification
- Placeholder for CrossRef, Google Search integration
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Optional

import requests
from loguru import logger


class FactCheckService:
    """Service for verifying facts against external APIs."""
    
    def __init__(self):
        self.wiki_session = requests.Session()
        self.wiki_session.headers.update({
            "User-Agent": "VideoFactory/1.0 (fact-checking bot; research verification)"
        })
        self._cache: dict[str, dict] = {}
        self._cache_ttl_hours = 24
    
    def check_wikipedia(self, query: str, language: str = "es") -> dict:
        """Check if term exists on Wikipedia.
        
        Args:
            query: Term to search (e.g., "Universidad de Pekín")
            language: Wiki language code (es, en, etc.)
            
        Returns:
            Dict with keys: found (bool), title, url, extract
        """
        cache_key = f"wiki:{language}:{query.lower().strip()}"
        
        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.time() - cached["cached_at"] < self._cache_ttl_hours * 3600:
                logger.debug(f"Wikipedia cache hit: {query}")
                return cached["result"]
        
        try:
            # Search for the term
            search_url = f"https://{language}.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 3
            }
            
            response = self.wiki_session.get(search_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            search_results = data.get("query", {}).get("search", [])
            
            if not search_results:
                # Try English fallback
                if language != "en":
                    return self.check_wikipedia(query, "en")
                
                result = {"found": False, "title": None, "url": None, "extract": None}
                self._cache[cache_key] = {"result": result, "cached_at": time.time()}
                return result
            
            # Get the best match
            best_match = search_results[0]
            title = best_match["title"]
            
            # Calculate similarity (simple contains check)
            query_lower = query.lower()
            title_lower = title.lower()
            
            # Check if it's actually related
            is_related = (
                query_lower in title_lower or 
                title_lower in query_lower or
                any(query_lower in r["title"].lower() for r in search_results[:2])
            )
            
            if not is_related:
                if language != "en":
                    return self.check_wikipedia(query, "en")
                
                result = {"found": False, "title": None, "url": None, "extract": None}
                self._cache[cache_key] = {"result": result, "cached_at": time.time()}
                return result
            
            # Get page details
            page_url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            
            result = {
                "found": True,
                "title": title,
                "url": page_url,
                "extract": best_match.get("snippet", "").replace("<span class=\"searchmatch\">", "").replace("</span>", "")
            }
            
            self._cache[cache_key] = {"result": result, "cached_at": time.time()}
            logger.debug(f"Wikipedia verified: {title}")
            return result
            
        except Exception as e:
            logger.warning(f"Wikipedia API error for '{query}': {e}")
            return {"found": False, "title": None, "url": None, "extract": None, "error": str(e)}
    
    def check_crossref(self, title: str, year: Optional[int] = None) -> dict:
        """Check if academic paper exists on CrossRef.
        
        Args:
            title: Paper/work title
            year: Publication year (optional filter)
            
        Returns:
            Dict with found status and metadata
        """
        cache_key = f"crossref:{title.lower().strip()}:{year}"
        
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.time() - cached["cached_at"] < self._cache_ttl_hours * 3600:
                return cached["result"]
        
        try:
            url = "https://api.crossref.org/works"
            params = {
                "query.title": title,
                "rows": 5,
                "filter": f"from-pub-date:{year}-01-01" if year else None
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            items = data.get("message", {}).get("items", [])
            
            if not items:
                result = {"found": False, "doi": None, "title": None}
                self._cache[cache_key] = {"result": result, "cached_at": time.time()}
                return result
            
            best = items[0]
            result = {
                "found": True,
                "doi": best.get("DOI"),
                "title": best.get("title", [None])[0],
                "publisher": best.get("publisher"),
                "year": best.get("published-print", {}).get("date-parts", [[None]])[0][0]
            }
            
            self._cache[cache_key] = {"result": result, "cached_at": time.time()}
            logger.debug(f"CrossRef found: {result['title']}")
            return result
            
        except Exception as e:
            logger.warning(f"CrossRef API error: {e}")
            return {"found": False, "doi": None, "title": None, "error": str(e)}
    
    def check_duckduckgo(self, query: str) -> dict:
        """Search DuckDuckGo Instant Answer for quick verification.
        
        Note: DDG doesn't have an official API, this uses their instant answer endpoint
        which has rate limits. Use sparingly.
        """
        cache_key = f"ddg:{query.lower().strip()}"
        
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.time() - cached["cached_at"] < self._cache_ttl_hours * 3600:
                return cached["result"]
        
        try:
            # DuckDuckGo Instant Answer API
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            # DDG returns JSONP, need to parse
            text = response.text
            if "callback(" in text:
                text = text.split("callback(")[1].rsplit(")", 1)[0]
            
            data = json.loads(text)
            
            abstract = data.get("Abstract", "")
            related_topics = data.get("RelatedTopics", [])
            
            has_result = bool(abstract) or len(related_topics) > 0
            
            result = {
                "found": has_result,
                "abstract": abstract[:200] if abstract else None,
                "source": data.get("AbstractSource"),
                "url": data.get("AbstractURL")
            }
            
            self._cache[cache_key] = {"result": result, "cached_at": time.time()}
            return result
            
        except Exception as e:
            logger.debug(f"DDG search failed: {e}")
            return {"found": False, "error": str(e)}
    
    def batch_verify(self, queries: list[str]) -> list[dict]:
        """Verify multiple queries efficiently.
        
        Args:
            queries: List of terms to verify
            
        Returns:
            List of result dicts in same order
        """
        results = []
        for query in queries:
            # Try Wikipedia first (fastest and most reliable)
            result = self.check_wikipedia(query)
            
            # If not found, try DDG as fallback
            if not result["found"]:
                result = self.check_duckduckgo(query)
            
            results.append(result)
            
            # Rate limiting - be nice to APIs
            time.sleep(0.5)
        
        return results
