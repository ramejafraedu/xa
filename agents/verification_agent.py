"""Video Factory V16 — Verification Agent.

Validates factual claims in scripts before video generation.
Protects "Faceless Channels" from spreading misinformation.

Module Contract:
  Input:  Script text + hook
  Output: VerificationReport with entity-level validation
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from loguru import logger

from services.fact_check_service import FactCheckService


class VerificationStatus(str, Enum):
    VERIFIED = "verified"           # ✅ Found and matches
    UNVERIFIED = "unverified"     # ⚠️ Not found, no contradiction
    CONTRADICTORY = "contradictory"  # ❌ Found but contradicts
    PARTIAL = "partial"           # 🟡 Partially verified (fuzzy match)


@dataclass
class EntityResult:
    """Result of verifying a single entity."""
    entity_type: str          # "percentage", "university", "researcher", "study_year", "term"
    original_text: str        # Text as it appears in script
    extracted_value: str      # Normalized value
    status: VerificationStatus
    confidence: float         # 0.0 to 1.0
    sources: list[dict] = field(default_factory=list)
    suggestion: str = ""      # Alternative phrasing if needed


@dataclass
class VerificationReport:
    """Complete verification report for a script."""
    overall_score: float      # 0-100
    entities: list[EntityResult]
    recommendation: str       # "APPROVE", "REVISE", "REJECT"
    summary: str              # Human-readable summary
    unverified_count: int = 0
    contradictory_count: int = 0


class VerificationAgent:
    """Verify factual claims in video scripts.
    
    Usage:
        agent = VerificationAgent()
        report = agent.run(script_text, hook)
        if report.recommendation == "APPROVE":
            # Continue with video generation
    """

    def __init__(self):
        self.fact_service = FactCheckService()
        
    def run(self, script_text: str, hook: str) -> VerificationReport:
        """Run full verification pipeline on a script.
        
        Args:
            script_text: Full script body
            hook: The hook text (often contains key claims)
            
        Returns:
            VerificationReport with all findings
        """
        t0 = logger.time()
        
        # 1. Extract all verifiable entities
        entities = self._extract_entities(script_text, hook)
        logger.info(f"🔍 Extracted {len(entities)} verifiable entities")
        
        # 2. Verify each entity (sequential for rate limiting)
        results = []
        for entity in entities:
            result = self._verify_entity(entity)
            results.append(result)
            
        # 3. Calculate overall metrics
        score = self._calculate_score(results)
        unverified = sum(1 for r in results if r.status == VerificationStatus.UNVERIFIED)
        contradictory = sum(1 for r in results if r.status == VerificationStatus.CONTRADICTORY)
        
        # 4. Generate recommendation
        recommendation = self._make_recommendation(score, unverified, contradictory)
        
        # 5. Build human-readable summary
        summary = self._build_summary(results, score, recommendation)
        
        elapsed = logger.time() - t0
        logger.info(f"✅ Verification complete: {score:.0f}% score, {unverified} unverified, {elapsed:.1f}s")
        
        return VerificationReport(
            overall_score=score,
            entities=results,
            recommendation=recommendation,
            summary=summary,
            unverified_count=unverified,
            contradictory_count=contradictory
        )
    
    def _extract_entities(self, script: str, hook: str) -> list[dict]:
        """Extract all verifiable entities from script.
        
        Returns list of dicts with keys: type, text, value
        """
        entities = []
        combined_text = f"{hook} {script}"
        
        # Pattern 1: Percentages with claims (e.g., "73% de los hijos únicos")
        percentage_pattern = r'(\d+(?:\.\d+)?)\s*%\s*(?:de\s+)?([^,.;]+)'
        for match in re.finditer(percentage_pattern, combined_text, re.IGNORECASE):
            value = match.group(1)
            context = match.group(2).strip()[:50]
            entities.append({
                "type": "percentage",
                "text": match.group(0),
                "value": f"{value}%",
                "context": context
            })
        
        # Pattern 2: Universities (common patterns)
        uni_patterns = [
            r'Universidad de ([^,.;]+)',
            r'University of ([^,.;]+)',
            r'([\w\s]+University)',
            r'([\w\s]+College)',
            r'MIT|Harvard|Stanford|Oxford|Cambridge',
        ]
        for pattern in uni_patterns:
            for match in re.finditer(pattern, combined_text, re.IGNORECASE):
                entities.append({
                    "type": "university",
                    "text": match.group(0),
                    "value": match.group(0).strip()
                })
        
        # Pattern 3: Year + study/research
        year_study_pattern = r'(\d{4}).{0,30}(?:estudio|investigacion|research|study|publico)'
        for match in re.finditer(year_study_pattern, combined_text, re.IGNORECASE):
            entities.append({
                "type": "study_year",
                "text": match.group(0),
                "value": match.group(1)
            })
        
        # Pattern 4: Named effects/conditions (capitalized terms)
        effect_pattern = r'Efecto ([A-Z][a-z]+(?:-[A-Z][a-z]+)?)|([A-Z][a-z]+ Syndrome)|([A-Z][a-z]+ Effect)'
        for match in re.finditer(effect_pattern, combined_text):
            effect_name = match.group(0)
            entities.append({
                "type": "psychological_term",
                "text": effect_name,
                "value": effect_name
            })
        
        # Pattern 5: Large numbers with claims
        number_pattern = r'(\d+(?:\.\d+)?)\s*(millones|millon|miles|thousand|million|billones?)'
        for match in re.finditer(number_pattern, combined_text, re.IGNORECASE):
            entities.append({
                "type": "large_number",
                "text": match.group(0),
                "value": match.group(0).replace(" ", "")
            })
        
        # Pattern 6: Researcher names (Dr. / PhD patterns)
        researcher_pattern = r'(?:Dr\.?|Doctor|PhD\.?)\s+([A-Z][a-z]+\s+(?:[A-Z][a-z]+)?)'
        for match in re.finditer(researcher_pattern, combined_text):
            entities.append({
                "type": "researcher",
                "text": match.group(0),
                "value": match.group(1).strip()
            })
        
        # Deduplicate by value
        seen = set()
        unique = []
        for e in entities:
            key = f"{e['type']}:{e['value'].lower()}"
            if key not in seen:
                seen.add(key)
                unique.append(e)
        
        return unique[:15]  # Limit to prevent API abuse
    
    def _verify_entity(self, entity: dict) -> EntityResult:
        """Verify a single entity against external sources."""
        entity_type = entity["type"]
        value = entity["value"]
        text = entity["text"]
        
        try:
            if entity_type == "university":
                return self._verify_university(value, text)
            elif entity_type == "percentage":
                return self._verify_percentage(value, entity.get("context", ""), text)
            elif entity_type == "psychological_term":
                return self._verify_psychological_term(value, text)
            elif entity_type == "study_year":
                return self._verify_study_year(value, text)
            elif entity_type == "researcher":
                return self._verify_researcher(value, text)
            else:
                return EntityResult(
                    entity_type=entity_type,
                    original_text=text,
                    extracted_value=value,
                    status=VerificationStatus.UNVERIFIED,
                    confidence=0.0,
                    suggestion=""
                )
        except Exception as e:
            logger.debug(f"Verification error for {entity_type}:{value}: {e}")
            return EntityResult(
                entity_type=entity_type,
                original_text=text,
                extracted_value=value,
                status=VerificationStatus.UNVERIFIED,
                confidence=0.0,
                suggestion="No se pudo verificar (error de API)"
            )
    
    def _verify_university(self, name: str, original: str) -> EntityResult:
        """Verify university exists via Wikipedia."""
        result = self.fact_service.check_wikipedia(name)
        
        if result["found"]:
            return EntityResult(
                entity_type="university",
                original_text=original,
                extracted_value=name,
                status=VerificationStatus.VERIFIED,
                confidence=0.95,
                sources=[{"source": "Wikipedia", "url": result.get("url", "")}],
                suggestion=""
            )
        else:
            return EntityResult(
                entity_type="university",
                original_text=original,
                extracted_value=name,
                status=VerificationStatus.UNVERIFIED,
                confidence=0.3,
                suggestion=f"Verificar nombre: ¿'{name}' es correcto?"
            )
    
    def _verify_percentage(self, value: str, context: str, original: str) -> EntityResult:
        """Try to verify percentage claim."""
        # Most percentages in viral content are unverifiable without full study access
        # We mark as partial and suggest hedging language
        return EntityResult(
            entity_type="percentage",
            original_text=original,
            extracted_value=value,
            status=VerificationStatus.PARTIAL,
            confidence=0.4,
            suggestion=f"Considerar: 'alrededor del {value}' o 'estudios sugieren {value}'"
        )
    
    def _verify_psychological_term(self, term: str, original: str) -> EntityResult:
        """Verify psychological effect/term exists."""
        result = self.fact_service.check_wikipedia(term)
        
        if result["found"]:
            return EntityResult(
                entity_type="psychological_term",
                original_text=original,
                extracted_value=term,
                status=VerificationStatus.VERIFIED,
                confidence=0.9,
                sources=[{"source": "Wikipedia", "url": result.get("url", "")}],
                suggestion=""
            )
        else:
            return EntityResult(
                entity_type="psychological_term",
                original_text=original,
                extracted_value=term,
                status=VerificationStatus.UNVERIFIED,
                confidence=0.2,
                suggestion=f"Verificar término '{term}' - ¿posible nombre alternativo?"
            )
    
    def _verify_study_year(self, year: str, original: str) -> EntityResult:
        """Verify study year is plausible."""
        try:
            year_int = int(year)
            current_year = 2024
            
            # Check if year is plausible (not future, not too old)
            if 1900 <= year_int <= current_year:
                return EntityResult(
                    entity_type="study_year",
                    original_text=original,
                    extracted_value=year,
                    status=VerificationStatus.VERIFIED,
                    confidence=0.7,
                    suggestion=""
                )
            else:
                return EntityResult(
                    entity_type="study_year",
                    original_text=original,
                    extracted_value=year,
                    status=VerificationStatus.CONTRADICTORY,
                    confidence=0.9,
                    suggestion=f"Año {year} parece incorrecto"
                )
        except ValueError:
            return EntityResult(
                entity_type="study_year",
                original_text=original,
                extracted_value=year,
                status=VerificationStatus.UNVERIFIED,
                confidence=0.0,
                suggestion=""
            )
    
    def _verify_researcher(self, name: str, original: str) -> EntityResult:
        """Verify researcher exists (basic check)."""
        # Simplified - just check if name format is plausible
        name_parts = name.split()
        if len(name_parts) >= 1 and len(name_parts) <= 3:
            return EntityResult(
                entity_type="researcher",
                original_text=original,
                extracted_value=name,
                status=VerificationStatus.PARTIAL,
                confidence=0.5,
                suggestion="Verificar nombre del investigador"
            )
        else:
            return EntityResult(
                entity_type="researcher",
                original_text=original,
                extracted_value=name,
                status=VerificationStatus.UNVERIFIED,
                confidence=0.2,
                suggestion="Formato de nombre inusual - verificar"
            )
    
    def _calculate_score(self, results: list[EntityResult]) -> float:
        """Calculate overall verification score (0-100)."""
        if not results:
            return 100.0  # Nothing to verify = perfect score
        
        weights = {
            VerificationStatus.VERIFIED: 1.0,
            VerificationStatus.PARTIAL: 0.6,
            VerificationStatus.UNVERIFIED: 0.2,
            VerificationStatus.CONTRADICTORY: 0.0
        }
        
        total_weight = sum(weights[r.status] for r in results)
        max_weight = len(results)
        
        return (total_weight / max_weight) * 100
    
    def _make_recommendation(self, score: float, unverified: int, contradictory: int) -> str:
        """Generate recommendation based on verification results."""
        if contradictory > 0:
            return "REJECT"
        elif score >= 80 and unverified == 0:
            return "APPROVE"
        elif score >= 60:
            return "REVISE"
        else:
            return "REJECT"
    
    def _build_summary(self, results: list[EntityResult], score: float, recommendation: str) -> str:
        """Build human-readable summary."""
        verified = [r for r in results if r.status == VerificationStatus.VERIFIED]
        partial = [r for r in results if r.status == VerificationStatus.PARTIAL]
        unverified = [r for r in results if r.status == VerificationStatus.UNVERIFIED]
        contradictory = [r for r in results if r.status == VerificationStatus.CONTRADICTORY]
        
        lines = [
            f"📊 Score de verificación: {score:.0f}%",
            f"✅ Verificados: {len(verified)}",
            f"🟡 Parciales: {len(partial)}",
            f"⚠️  No verificados: {len(unverified)}",
        ]
        
        if contradictory:
            lines.append(f"❌ Contradictorios: {len(contradictory)}")
        
        lines.append(f"\n🎯 Recomendación: {recommendation}")
        
        if unverified:
            lines.append("\n⚠️  Entidades no verificadas:")
            for r in unverified[:3]:
                lines.append(f"  • '{r.original_text}' - {r.suggestion}")
        
        return "\n".join(lines)
