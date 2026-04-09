"""Video Factory V16 — Free APIs Integration.

Additional free APIs for video stock, facts, and data.
All APIs here have free tiers or are completely free.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from loguru import logger


class MixkitAPI:
    """Mixkit.co - Free video stock (no API key required, screen scraping friendly).
    
    Note: Mixkit doesn't have an official API, but provides direct download links.
    We'll use a simple search-based approach.
    """
    
    BASE_URL = "https://mixkit.co/free-stock-video"
    
    @staticmethod
    def search_videos(keyword: str, max_results: int = 5) -> list[dict]:
        """Search Mixkit for free videos.
        
        Returns list of video metadata with direct download URLs.
        """
        # Mixkit doesn't have a search API, so we use their tag-based browsing
        # This is a simplified implementation
        
        logger.debug(f"Mixkit search: {keyword}")
        
        # Map common keywords to Mixkit tags
        tag_mapping = {
            "nature": "nature",
            "people": "people",
            "technology": "technology",
            "business": "business-work",
            "city": "city",
            "abstract": "abstract",
            "food": "food-drink",
            "sports": "sports",
            "animals": "animals",
        }
        
        # Try to find matching tag
        tag = None
        keyword_lower = keyword.lower()
        for kw, mixkit_tag in tag_mapping.items():
            if kw in keyword_lower:
                tag = mixkit_tag
                break
        
        if not tag:
            tag = "popular"  # Default to popular videos
        
        try:
            url = f"{MixkitAPI.BASE_URL}/{tag}/"
            response = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            
            # Extract video URLs from page (simplified parsing)
            # In production, use proper HTML parsing with BeautifulSoup
            videos = []
            
            # Mock results for now - in real implementation parse HTML
            logger.info(f"Mixkit: searching tag '{tag}'")
            
            return videos
            
        except Exception as e:
            logger.warning(f"Mixkit API error: {e}")
            return []


class NumbersAPI:
    """NumbersAPI.com - Interesting facts about numbers.
    
    Free API, no key required.
    Useful for: 'Did you know 73 appears in X contexts?'
    """
    
    BASE_URL = "http://numbersapi.com"
    
    @staticmethod
    def get_fact(number: int, type_: str = "trivia") -> Optional[str]:
        """Get a trivia fact about a number.
        
        Args:
            number: The number to get facts about
            type_: 'trivia', 'math', 'date', 'year'
            
        Returns:
            Fact string or None if failed
        """
        try:
            url = f"{NumbersAPI.BASE_URL}/{number}/{type_}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.debug(f"NumbersAPI error: {e}")
            return None
    
    @staticmethod
    def get_random_fact(min_num: int = 1, max_num: int = 100) -> Optional[dict]:
        """Get a random number fact.
        
        Returns:
            Dict with 'number' and 'fact' keys
        """
        try:
            import random
            number = random.randint(min_num, max_num)
            fact = NumbersAPI.get_fact(number)
            if fact:
                return {"number": number, "fact": fact}
            return None
        except Exception as e:
            logger.debug(f"NumbersAPI random error: {e}")
            return None
    
    @staticmethod
    def find_hook_number(topic: str) -> Optional[dict]:
        """Try to find an interesting number related to a topic.
        
        Example: topic="psychology" might return number about studies count
        """
        # Common psychology/curiosity numbers
        interesting_numbers = {
            "7": "number of items in short-term memory",
            "21": "days to form a habit (popular myth)",
            "73": "most commonly cited percentage in fake stats",
            "10": "10,000 hours to mastery rule",
            "50": "50% of people believe X (common claim)",
            "90": "90% of communication is non-verbal (popular myth)",
            "3": "rule of three in communication",
            "5": "five stages of grief",
        }
        
        topic_lower = topic.lower()
        
        # Try to match topic to a known fact
        for num, context in interesting_numbers.items():
            if any(word in topic_lower for word in context.split()):
                fact = NumbersAPI.get_fact(int(num), "trivia")
                if fact:
                    return {"number": num, "fact": fact, "context": context}
        
        # Fallback: get random number fact
        return NumbersAPI.get_random_fact(1, 100)


class UselessFactsAPI:
    """UselessFacts.jsph.pl - Random useless facts.
    
    Free API, no key required.
    Good for entertainment/curiosity content.
    """
    
    BASE_URL = "https://uselessfacts.jsph.pl"
    
    @staticmethod
    def get_random_fact(language: str = "en") -> Optional[str]:
        """Get a random useless fact."""
        try:
            url = f"{UselessFactsAPI.BASE_URL}/api/v2/facts/random"
            params = {"language": language}
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            return data.get("text")
        except Exception as e:
            logger.debug(f"UselessFacts API error: {e}")
            return None
    
    @staticmethod
    def get_today_fact(language: str = "en") -> Optional[str]:
        """Get today's fact."""
        try:
            url = f"{UselessFactsAPI.BASE_URL}/api/v2/facts/today"
            params = {"language": language}
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            return data.get("text")
        except Exception as e:
            logger.debug(f"UselessFacts today error: {e}")
            return None


class FunFactsAPI:
    """API Ninjas Fun Facts - Random fun facts.
    
    Requires API key (free tier: 10K requests/month).
    """
    
    BASE_URL = "https://api.api-ninjas.com/v1/facts"
    
    @staticmethod
    def get_fact(api_key: Optional[str] = None) -> Optional[str]:
        """Get a random fun fact."""
        from config import settings
        
        key = api_key or getattr(settings, 'api_ninjas_key', None)
        if not key:
            logger.debug("API Ninjas key not configured")
            return None
        
        try:
            headers = {"X-Api-Key": key}
            response = requests.get(FunFactsAPI.BASE_URL, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data and len(data) > 0:
                return data[0].get("fact")
            return None
        except Exception as e:
            logger.debug(f"API Ninjas error: {e}")
            return None


class WikidataAPI:
    """Wikidata API - Structured data from Wikipedia.
    
    Completely free, no rate limits for reasonable use.
    Good for fact verification and data enrichment.
    """
    
    BASE_URL = "https://www.wikidata.org/w/api.php"
    
    @staticmethod
    def search_entities(query: str, limit: int = 5) -> list[dict]:
        """Search for entities on Wikidata."""
        try:
            params = {
                "action": "wbsearchentities",
                "format": "json",
                "language": "es",
                "search": query,
                "limit": limit
            }
            response = requests.get(WikidataAPI.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("search", [])
        except Exception as e:
            logger.debug(f"Wikidata search error: {e}")
            return []
    
    @staticmethod
    def get_entity_data(entity_id: str) -> Optional[dict]:
        """Get detailed data for an entity."""
        try:
            params = {
                "action": "wbgetentities",
                "format": "json",
                "ids": entity_id,
                "languages": "es|en",
                "props": "labels|descriptions|claims"
            }
            response = requests.get(WikidataAPI.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("entities", {}).get(entity_id)
        except Exception as e:
            logger.debug(f"Wikidata entity error: {e}")
            return None
    
    @staticmethod
    def verify_psychology_term(term: str) -> Optional[dict]:
        """Verify if a psychology term exists on Wikidata.
        
        Returns entity data if found, None otherwise.
        """
        # Search for term
        results = WikidataAPI.search_entities(term, limit=3)
        
        if not results:
            return None
        
        # Check if any result is related to psychology
        for result in results:
            entity_id = result.get("id")
            data = WikidataAPI.get_entity_data(entity_id)
            
            if not data:
                continue
            
            # Check if it's a psychological concept
            claims = data.get("claims", {})
            
            # Look for instance of (P31) or subclass of (P279)
            # psychological concept = Q179313
            for prop in ["P31", "P279"]:
                if prop in claims:
                    for claim in claims[prop]:
                        mainsnak = claim.get("mainsnak", {})
                        datavalue = mainsnak.get("datavalue", {})
                        value = datavalue.get("value", {})
                        if value.get("id") == "Q179313":  # psychological concept
                            return {
                                "found": True,
                                "entity_id": entity_id,
                                "label": result.get("label"),
                                "description": result.get("description"),
                                "url": f"https://www.wikidata.org/wiki/{entity_id}"
                            }
        
        return None


class FreeAPIAggregator:
    """Aggregate multiple free APIs for content enrichment."""
    
    def __init__(self):
        self.cache: dict[str, tuple] = {}  # Simple cache
        self.cache_ttl = 3600  # 1 hour
    
    def get_hook_suggestion(self, topic: str = "") -> Optional[str]:
        """Get a hook suggestion using free APIs.
        
        Tries multiple sources in order of preference.
        """
        cache_key = f"hook:{topic}"
        if cache_key in self.cache:
            result, timestamp = self.cache[cache_key]
            if time.time() - timestamp < self.cache_ttl:
                return result
        
        # Try NumbersAPI first (reliable, no key)
        if not topic or "number" in topic.lower():
            fact = NumbersAPI.find_hook_number(topic)
            if fact:
                result = f"¿Sabías que el número {fact['number']} {fact['fact']}?"
                self.cache[cache_key] = (result, time.time())
                return result
        
        # Try UselessFacts
        fact = UselessFactsAPI.get_random_fact(language="en")
        if fact:
            # Translate or use as-is
            result = f"Dato curioso: {fact}"
            self.cache[cache_key] = (result, time.time())
            return result
        
        # Try FunFactsAPI
        fact = FunFactsAPI.get_fact()
        if fact:
            result = f"¿Sabías que {fact}?"
            self.cache[cache_key] = (result, time.time())
            return result
        
        return None
    
    def enrich_script(self, script: str) -> dict:
        """Enrich script with additional facts and data.
        
        Returns dict with enrichment data.
        """
        enrichment = {
            "facts_added": [],
            "verified_terms": [],
            "suggestions": []
        }
        
        # Extract potential terms to verify
        import re
        capitalized_terms = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', script)
        
        # Verify first 3 unique terms
        seen = set()
        for term in capitalized_terms:
            term_lower = term.lower()
            if term_lower in seen or len(term_lower) < 5:
                continue
            seen.add(term_lower)
            
            # Verify with Wikidata
            verified = WikidataAPI.verify_psychology_term(term)
            if verified:
                enrichment["verified_terms"].append({
                    "term": term,
                    "source": verified.get("url")
                })
            
            if len(enrichment["verified_terms"]) >= 3:
                break
        
        return enrichment


# Convenience function
def get_free_hook(topic: str = "") -> Optional[str]:
    """Quick function to get a hook from free APIs."""
    aggregator = FreeAPIAggregator()
    return aggregator.get_hook_suggestion(topic)
