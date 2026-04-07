# 📱 Visual Skill: TikTok & Reels Style Guide
# Reference para AssetAgent y EditorAgent.
# Define las zonas seguras, paletas y estilo visual por plataforma.

## Zonas Seguras de TikTok/Reels (1080x1920)

```
┌────────────────────┐ ← y=0
│  ZONA PELIGROSA    │   Primeros 130px: solapado por navbar superior
│  (no poner texto)  │
├────────────────────┤ ← y=130
│                    │
│   ZONA SEGURA      │
│   para subtítulos  │
│   y texto          │
│                    │
├────────────────────┤ ← y=1620
│  ZONA PELIGROSA    │   Últimos 300px: botones de like/share/follow
│  (no poner texto)  │
└────────────────────┘ ← y=1920
```

**Subtítulos seguros:** y entre 130 y 1620 (margen de 50px extra recomendado)
**Área de uso: y=180 a y=1570**

## Paletas de Color por Nicho

### Finanzas (Premium Dark)
- Fondo: #0A0A0A o #1A1A2E
- Texto: #F5F5F0 (blanco roto, no puro)
- Acento: #C9A84C (dorado antiguo)
- Evitar: azules brillantes, rojos, colores saturados

### Historia / Misterio
- Fondo: #0D0D0D
- Texto: #E8E8E8
- Acento: #8B0000 (rojo oscuro) o #4A4A8A (azul noche)
- Overlay: sepia o desaturación del 30-40%

### Curiosidades
- Paleta contrastante: oscuro + acento brillante
- Acento: #00E5FF (cyan) o #FF6B35 (naranja)
- El contraste alto retiene la atención visual

### Salud
- Tonos naturales: verde (#4CAF50), blanco, beige
- Fondo claro preferred
- Transmite limpieza, naturalidad y confianza

### Recetas
- Colores cálidos: naranjas, rojos, amarillos
- Iluminación cálida en los clips
- El color del alimento es el protagonista

## Tipografía de Subtítulos (ASS)

```
FontName=Montserrat Black
FontSize=72
Bold=1
PrimaryColour=&H00FFFFFF    (blanco)
OutlineColour=&H00000000    (negro)
BackColour=&H80000000       (semi-transparente)
Outline=3
Shadow=1
Alignment=2                  (centrado abajo)
MarginV=80                   (margen inferior)
```

## Keywords para Stock Video por Nicho

### Finanzas
- luxury lifestyle, wealth, city panorama, financial district
- businessman, gold, stock market, real estate
- EVITAR: imágenes genéricas de dinero volando

### Historia
- historical archive, documentary, dark atmosphere, old documents
- investigation board, shadow, vintage, mystery

### Curiosidades
- brain, science, psychology, mind, optical illusion
- data visualization, neurons, experiment

### Salud
- healthy food, nature, exercise, sunrise, meditation
- fresh vegetables, water, green, organic

### Recetas
- cooking, kitchen, food close-up, chef hands, ingredients
- steam, fresh, delicious, plating

## Efectos Visuales Recomendados (FFmpeg)

- **Zoom sutil:** `scale=1.05*iw:1.05*ih,crop=iw/1.05:ih/1.05` (da dinamismo sin molestar)
- **Fade in/out:** para primeros y últimos 0.5s
- **Color grade oscuro:** `curves=vintage` o `hue=s=0.85` (satura levemente para TikTok)
