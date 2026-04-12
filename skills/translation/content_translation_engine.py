"""
Content Translation Engine - ShortGPT Integration
Traducción automática de contenido de video a múltiples idiomas
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from loguru import logger


@dataclass
class TranslatedContent:
    """Contenido traducido con metadata"""
    original_text: str
    translated_text: str
    target_language: str
    engine: str
    confidence: float = 0.0


class ContentTranslationEngine:
    """
    Motor de traducción de contenido para videos multi-idioma.
    
    Soporta: ES (default), EN, PT, FR
    """
    
    SUPPORTED_LANGUAGES = {
        "es": {
            "name": "Español",
            "voice": "es-ES-AlvaroNeural",
            "fallback": "es-ES-ElviraNeural"
        },
        "en": {
            "name": "English", 
            "voice": "en-US-GuyNeural",
            "fallback": "en-US-JennyNeural"
        },
        "pt": {
            "name": "Português",
            "voice": "pt-BR-AntonioNeural", 
            "fallback": "pt-BR-FranciscaNeural"
        },
        "fr": {
            "name": "Français",
            "voice": "fr-FR-HenriNeural",
            "fallback": "fr-FR-DeniseNeural"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.cache: Dict[str, TranslatedContent] = {}
        
    def is_supported(self, language_code: str) -> bool:
        """Verifica si el idioma es soportado"""
        return language_code.lower() in self.SUPPORTED_LANGUAGES
    
    def get_voice_for_language(self, language_code: str, use_fallback: bool = False) -> str:
        """Obtiene el voice ID para un idioma"""
        lang = self.SUPPORTED_LANGUAGES.get(language_code.lower())
        if not lang:
            return "en-US-GuyNeural"  # Default
        
        return lang["fallback"] if use_fallback else lang["voice"]
    
    def translate_script(
        self,
        script: str,
        target_language: str,
        preserve_formatting: bool = True
    ) -> TranslatedContent:
        """
        Traduce un guión completo manteniendo estructura.
        
        Args:
            script: Texto del guión
            target_language: Código de idioma (es/en/pt/fr)
            preserve_formatting: Mantener saltos de línea y párrafos
            
        Returns:
            TranslatedContent con el texto traducido
        """
        if not self.is_supported(target_language):
            logger.warning(f"Idioma {target_language} no soportado, retornando original")
            return TranslatedContent(
                original_text=script,
                translated_text=script,
                target_language=target_language,
                engine="none",
                confidence=0.0
            )
        
        # Cache key
        cache_key = f"{hash(script)}_{target_language}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Detectar estructura del guión
        sections = self._parse_script_structure(script) if preserve_formatting else [script]
        
        # Traducir cada sección
        translated_sections = []
        for section in sections:
            translated = self._translate_text(section, target_language)
            translated_sections.append(translated)
        
        result = "\n\n".join(translated_sections) if preserve_formatting else translated_sections[0]
        
        translated_content = TranslatedContent(
            original_text=script,
            translated_text=result,
            target_language=target_language,
            engine="shortgpt",
            confidence=0.85
        )
        
        self.cache[cache_key] = translated_content
        
        logger.info(f"Script traducido: {len(script)} chars -> {target_language}")
        
        return translated_content
    
    def translate_with_eml(
        self,
        script: str,
        eml_data: dict,
        target_language: str
    ) -> tuple[str, dict]:
        """
        Traduce guión + EML manteniendo sincronía temporal.
        
        Args:
            script: Texto del guión
            eml_data: Datos EML con timing
            target_language: Idioma destino
            
        Returns:
            (script_traducido, eml_actualizado)
        """
        # Traducir el guión
        translated = self.translate_script(script, target_language)
        
        # Ajustar EML manteniendo el timing pero actualizando el texto
        if eml_data and "scenes" in eml_data:
            original_sentences = script.split(".")
            translated_sentences = translated.translated_text.split(".")
            
            # Mapear oraciones traducidas a escenas EML
            for i, scene in enumerate(eml_data["scenes"]):
                if i < len(translated_sentences) and "subtitle" in scene:
                    scene["subtitle"]["text"] = translated_sentences[i].strip()
                    
                    # Re-calcular palabras manteniendo timing
                    if "words" in scene["subtitle"]:
                        words = translated_sentences[i].split()
                        original_words = scene["subtitle"]["words"]
                        
                        # Distribuir timing proporcionalmente
                        if len(words) == len(original_words):
                            for j, word in enumerate(words):
                                original_words[j]["word"] = word
                        elif original_words:
                            # Si diferente cantidad de palabras, ajustar
                            duration_per_word = (
                                original_words[-1]["end"] - original_words[0]["start"]
                            ) / len(words) if len(words) > 0 else 1.0
                            
                            start_time = original_words[0]["start"]
                            scene["subtitle"]["words"] = [
                                {
                                    "word": w,
                                    "start": start_time + (j * duration_per_word),
                                    "end": start_time + ((j + 1) * duration_per_word)
                                }
                                for j, w in enumerate(words)
                            ]
        
        return translated.translated_text, eml_data
    
    def batch_translate(
        self,
        scripts: List[str],
        target_languages: List[str]
    ) -> Dict[str, List[TranslatedContent]]:
        """
        Traduce múltiples guiones a múltiples idiomas.
        
        Returns:
            Dict: {language_code: [TranslatedContent, ...]}
        """
        results = {}
        
        for lang in target_languages:
            if not self.is_supported(lang):
                continue
                
            results[lang] = []
            for script in scripts:
                translated = self.translate_script(script, lang)
                results[lang].append(translated)
        
        return results
    
    def _parse_script_structure(self, script: str) -> List[str]:
        """Parsea el guión en secciones (hook, body, cta)"""
        # Separar por párrafos dobles o secciones marcadas
        sections = re.split(r'\n\n+', script.strip())
        return [s.strip() for s in sections if s.strip()]
    
    def _translate_text(self, text: str, target_language: str) -> str:
        """
        Traduce texto usando el motor configurado.
        
        Por ahora: stub que simula traducción.
        En producción: integrar con deep-translator o Gemini
        """
        # TODO: Implementar con deep-translator o Gemini API
        # from deep_translator import GoogleTranslator
        # translator = GoogleTranslator(source='auto', target=target_language)
        # return translator.translate(text)
        
        # Simulación para testing
        prefixes = {
            "es": "[ES] ",
            "en": "[EN] ",
            "pt": "[PT] ",
            "fr": "[FR] "
        }
        
        prefix = prefixes.get(target_language, f"[{target_language}] ")
        return prefix + text


# Helper para uso en pipeline
def translate_for_multilang(
    script: str,
    eml_data: Optional[dict] = None,
    languages: Optional[List[str]] = None
) -> Dict[str, any]:
    """
    Función helper para traducir contenido a múltiples idiomas.
    
    Args:
        script: Guión original
        eml_data: Datos EML opcionales
        languages: Lista de códigos de idioma (default: ["en", "pt", "fr"])
        
    Returns:
        Dict con traducciones y voces para cada idioma
    """
    if languages is None:
        languages = ["en", "pt", "fr"]
    
    engine = ContentTranslationEngine()
    
    results = {
        "original": {
            "text": script,
            "voice": engine.get_voice_for_language("es"),
            "language": "es"
        },
        "translations": {}
    }
    
    for lang in languages:
        if not engine.is_supported(lang):
            continue
            
        if eml_data:
            translated_script, updated_eml = engine.translate_with_eml(
                script, eml_data.copy(), lang
            )
            results["translations"][lang] = {
                "text": translated_script,
                "eml": updated_eml,
                "voice": engine.get_voice_for_language(lang),
                "language": lang
            }
        else:
            translated = engine.translate_script(script, lang)
            results["translations"][lang] = {
                "text": translated.translated_text,
                "voice": engine.get_voice_for_language(lang),
                "language": lang
            }
    
    return results
