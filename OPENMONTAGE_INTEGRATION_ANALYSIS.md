# Análisis de Integración OpenMontage → Video Factory V16 PRO

## Criterios de Selección de Componentes

### 1. Valor Técnico y Funcional
- **CLIP Embedder** (`lib/clip_embedder.py`): Motor de embeddings visuales/texto usando OpenAI CLIP. Permite búsqueda semántica por similitud de coseno, fundamental para sistemas de recuperación de assets inteligentes.
- **Corpus Manager** (`lib/corpus.py`): Infraestructura de indexación local con almacenamiento vectorial (embeddings.npy) + metadatos JSONL. Diseñado para corpora de cientos/thousands de clips con retrieval eficiente.

### 2. Reusabilidad y Mantenibilidad
- **Clip Cache** (`tools/video/clip_cache.py`): Sistema de caché LRU con TTL para clips descargados. Evita re-descargas, reduce costos de API y mejora velocidad de iteración.
- **Corpus Builder** (`tools/video/corpus_builder.py`): Orquesta descarga, thumbnail generation, embedding y indexado. Pipeline completo de ingestión de assets.

### 3. Rendimiento y Escalabilidad
- **Direct Clip Search** (`tools/video/direct_clip_search.py`): Búsqueda optimizada con batching y rate limiting. Integra múltiples fuentes (Pexels, Pixabay, etc.) con fallback.
- **Stock Sources** (`tools/video/stock_sources/`): Adaptadores modulares para APIs de stock video. Patrón adapter permite añadir nuevas fuentes sin modificar código existente.

### 4. Capacidades AI Avanzadas
- **Higgsfield Video** (`tools/video/higgsfield_video.py`): Generación de video AI vía API Higgsfield. Soporte para animación de imágenes estáticas.
- **Seedance Video** (`tools/video/seedance_video.py`): Generación de video AI vía Seedance. Alternativa para casos donde otros modelos fallan.

## Matriz de Priorización

| Componente | Impacto | Complejidad | Esfuerzo | Prioridad |
|------------|---------|-------------|----------|-----------|
| clip_embedder.py | Alto | Media | 2h | P0 |
| corpus.py | Alto | Media | 2h | P0 |
| clip_cache.py | Alto | Baja | 1h | P0 |
| corpus_builder.py | Alto | Media | 3h | P0 |
| clip_search.py | Medio | Baja | 1h | P1 |
| direct_clip_search.py | Medio | Media | 2h | P1 |
| higgsfield_video.py | Medio | Baja | 1h | P2 |
| seedance_video.py | Medio | Baja | 1h | P2 |
| stock_sources/ | Medio | Media | 3h | P2 |

## Justificación Técnica

### Por qué CLIP + Corpus Local
1. **Offline-first**: No depende de APIs externas para retrieval
2. **Costo**: Embeddings una vez, búsquedas ilimitadas sin costo
3. **Velocidad**: Búsqueda vectorial en numpy es O(n) con n<1000, suficiente para proyectos documentales
4. **Precisión**: CLIP ViT-B/32 proporciona similitud semántica de alta calidad

### Por qué Clip Cache
1. **Idempotencia**: Múltiples agentes pueden requerir mismo clip sin re-descarga
2. **Resiliencia**: Si API falla, cache sigue disponible
3. **Eficiencia**: TTL automático limpia archivos antiguos

### Arquitectura de Integración
```
┌─────────────────────────────────────────────────────────────┐
│                    Video Factory V16 PRO                      │
├─────────────────────────────────────────────────────────────┤
│  Nuevo Subsistema: Clip Intelligence Engine                 │
│  ┌───────────────────────────────────────────────────────┐   │
│  │ lib/clip_embedder.py  →  Embeddings CLIP 512-dim     │   │
│  │ lib/corpus.py         →  Vector Store + Metadata     │   │
│  └───────────────────────────────────────────────────────┘   │
│                         ↓                                   │
│  ┌───────────────────────────────────────────────────────┐   │
│  │ tools/video/corpus_builder.py → Ingesta pipelines     │   │
│  │ tools/video/clip_cache.py     → LRU Cache con TTL    │   │
│  │ tools/video/clip_search.py    → Query interface     │   │
│  │ tools/video/direct_clip_search → Multi-source search  │   │
│  └───────────────────────────────────────────────────────┘   │
│                         ↓                                   │
│  Integración con:                                           │
│  - pipeline_v16.py (nuevo paso de asset retrieval)          │
│  - state_manager.py (checkpoint de corpus)                  │
│  - cost_tracker.py (tracking de descargas API)            │
└─────────────────────────────────────────────────────────────┘
```

## Plan de Integración

### Fase 1: Core Infrastructure (P0)
- [ ] Integrar `clip_embedder.py`
- [ ] Integrar `corpus.py`
- [ ] Integrar `clip_cache.py`

### Fase 2: Builder & Search (P0-P1)
- [ ] Integrar `corpus_builder.py`
- [ ] Integrar `clip_search.py`
- [ ] Integrar `direct_clip_search.py`

### Fase 3: AI Generation & Sources (P2)
- [ ] Integrar `higgsfield_video.py`
- [ ] Integrar `seedance_video.py`
- [ ] Integrar `stock_sources/` directory

### Fase 4: Testing & Documentation
- [ ] Tests unitarios para cada componente
- [ ] Tests de integración end-to-end
- [ ] Documentación de uso y API
