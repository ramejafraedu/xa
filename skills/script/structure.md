# 🎬 Script Skill: Content Structure
# Template de estructura JSON que debe seguir ScriptAgent.
# Edita este archivo para cambiar el schema de salida.

## Output JSON Schema

El ScriptAgent DEBE devolver exactamente este schema JSON:

```json
{
  "titulo": "Título SEO optimizado (max 60 chars)",
  "gancho": "Primera oración impactante (8-15 palabras, ver hooks.md)",
  "guion": "El cuerpo del script. Prose continua, sin markdown. 150-300 palabras.",
  "cta": "Call to action final (max 20 palabras, acción específica)",
  "caption": "Caption para redes sociales con emojis y hashtags",
  "palabras_clave": ["kw1", "kw2", "kw3", "kw4", "kw5"],
  "mood_musica": "uno de: motivational | dark | ambient | upbeat | cinematic",
  "velocidad_cortes": "uno de: lento | medio | rapido | ultra_rapido",
  "num_clips": 8,
  "duraciones_clips": [4.5, 3.2, 5.0, 4.0, 3.5, 5.5, 4.0, 3.8],
  "viral_score": 85,
  "prompt_imagen": "Prompt en inglés para generación de imagen de apertura",
  "_ab_variant": "A"
}
```

## Guía de Campos

### guion
- Escrito como si se dijera en voz alta, no como texto leído
- Sin listas, sin bullets, sin markdown
- Transiciones naturales entre ideas
- Ritmo: alterna frases cortas (punch) con frases largas (contexto)
- NUNCA incluyas el gancho en el guion, ya está separado

### velocidad_cortes
- `lento` → finanzas old money, reflexión, narración pausada
- `medio` → historia, curiosidades sin urgencia
- `rapido` → curiosidades con datos, ia_herramientas con demos
- `ultra_rapido` → trending topics, datos en cadena

### viral_score
- Autoevalúa del 0-100
- < 60 = el script no should not pass quality gate
- 60-75 = aprobado pero mejorable
- 75-90 = bueno
- > 90 = excelente (reservar para contenido realmente único)

### duraciones_clips
- Suma total debe aproximarse a la duración del audio
- Clips de tensión: 2-4 segundos (ritmo rápido)
- Clips de revelación: 5-8 segundos (dejar respirar)
- Primer y último clip: siempre los más potentes visualmente
