# Remotion Skill

## When to Use

Use Remotion for advanced video composition from Phase 3 onward — anywhere that requires
React-based scene assembly, parametric templates, animated overlays, transitions, or
data-driven batch rendering. For simple cuts, burns, and encodes, prefer FFmpeg directly.

## Relationship to Remotion Agent Skills

The **installed agent skills** (`.agents/skills/remotion-best-practices/`) teach correct
Remotion API usage — imports, timing, animation constraints, code patterns.
**This file** teaches how OpenMontage uses Remotion — which compositions map to pipeline
stages, how artifacts flow in, and how renders are triggered.

## Remotion-First Routing

**Remotion is the DEFAULT composition engine for ALL final renders when available.**
It handles video clips (via `<OffthreadVideo>`), still images, animated scenes,
component types, transitions, and mixed content — all in a single React-based
render pass.

FFmpeg is the **fallback** — used only when Remotion is unavailable, or for
simple standalone operations that don't benefit from React rendering.

| Use Case | Backend | Why |
|----------|---------|-----|
| Final video render (any content type) | **Remotion** | Default for all compositions |
| Video clips + animated stills + text cards | **Remotion** | Mixed content in one pass |
| Video-only cuts with transitions | **Remotion** | Native `<OffthreadVideo>` + transitions |
| Animated diagrams/text cards | **Remotion** | Frame-by-frame control |
| Data-driven batch videos | **Remotion** | Zod props + parametric renders |
| Word-level captions (in composition) | **Remotion** | CaptionOverlay with word highlight — superior to SRT |
| Audio embedding (narration + music) | **Remotion** | Native `<Audio>` components with volume/fade |
| Simple trim, concat (no composition) | FFmpeg | Instant, no Node dependency |
| Subtitle burn-in (standalone, post-hoc) | FFmpeg | Only for adding subs to an already-rendered video without re-rendering |
| Face enhance, color grade | FFmpeg | Filter-based, deterministic |
| Remotion unavailable | FFmpeg | Automatic fallback |

**Note:** The `render` operation auto-routes to Remotion by default. FFmpeg is
only selected when Remotion is not installed or the agent explicitly calls
`operation='compose'` for standalone operations. The agent can also write custom
Remotion compositions on the fly via the capability-extension protocol when no
existing composition covers the layout (e.g., custom PiP, split-screen).

## Supported Scene Types (Cut Types)

The Explainer composition supports the following cut types:

| Type | Props Required | Best For |
|------|---------------|----------|
| `text_card` | `text` | Statements, titles, closing messages |
| `stat_card` | `stat`, optional `subtitle`, `accentColor` | Big numbers, impactful metrics |
| `hero_title` | `text`, optional `heroSubtitle` | Opening titles, dramatic reveals |
| `callout` | `text`, optional `title`, `callout_type` (info/warning/tip/quote) | Tips, quotes, important notes |
| `comparison` | `leftLabel`, `rightLabel`, `leftValue`, `rightValue` | Before/after, A/B, versus |
| `bar_chart` | `chartData` [{label, value}], optional `title`, `chartAnimation` | Category comparisons, rankings |
| `line_chart` | `chartSeries` [{label, data: [{x,y}]}], optional `title` | Trends, time series, growth |
| `pie_chart` | `chartData` [{label, value}], optional `donut`, `centerLabel` | Proportions, breakdowns |
| `kpi_grid` | `chartData` [{label, value, prefix, suffix, change, icon}] | Dashboards, traction metrics |
| `progress_bar` | `progress` (0-100), optional `progressSegments` | Journey viz, completion, stacked metrics |
| `anime_scene` | `images` (1-4 paths), optional `animation`, `particles`, `particleColor`, `particleCount`, `particleIntensity`, `vignette`, `lightingFrom`, `lightingTo` | Anime/Ghibli-style scenes with multi-image crossfade, camera motion, particle overlays |

**Chart animations:** `grow-up`, `slide-in`, `pop` (bar), `draw`, `fade-in` (line), `spin`, `expand`, `sequential` (pie), `count-up`, `pop`, `cascade` (kpi)

### Anime Scene — Multi-Image Crossfade + Particles

The `anime_scene` type renders 1-4 images with smooth crossfade transitions, cinematic camera motion, and animated particle overlays. This creates the illusion of animation from still images.

**Camera motion types:** `zoom-in`, `zoom-out`, `pan-left`, `pan-right`, `ken-burns`, `drift-up`, `drift-down`, `parallax`, `static`

**Particle types:** `fireflies` (floating golden orbs), `petals` (falling cherry blossoms), `sparkles` (twinkling stars), `mist` (drifting fog layers), `light-rays` (crepuscular rays)

**Key prop:** `sceneDurationSeconds` is automatically passed by `SceneRenderer` — this fixes a critical Remotion pitfall where `useVideoConfig().durationInFrames` returns the full composition duration, not the scene's Sequence duration.

**Multi-image crossfade math:** Each image owns an equal time segment. Fade-out of image N and fade-in of image N+1 OVERLAP by `crossfadeDur` (~1.2s) so there's never a dead frame. Generate 2-3 images per scene from the same visual system, but vary the shot, subject, and lighting per beat. Nearby seeds help create subtle motion without flattening the whole sequence into one repeated prompt.

**Reference composition:** `remotion-composer/public/demo-props/mori-no-seishin.json` — 6 anime scenes, 30 seconds, with particles, lighting, overlays, and ambient music.

**Style playbook:** `styles/anime-ghibli.yaml` — Ghibli-inspired aesthetic with color palette, typography, motion parameters, and FLUX prompt prefix.

**Zero-key video strategy:** When no image or video generation is available, build
entire videos from these component types. A well-composed sequence of hero_title →
kpi_grid → bar_chart → comparison → stat_card → text_card produces a polished,
professional video with zero external dependencies.

### The Proven Formula for Zero-Key Videos

These rules were discovered through systematic render testing and produce cinematic results:

**1. Commit to one background family per video.** Use a coherent background treatment derived from the playbook or custom identity instead of forcing every sequence into the same dark dashboard look.
This prevents jarring white↔dark flash transitions and makes chart colors pop dramatically.
The goal is visual cohesion, not a mandatory dark theme.

**2. Flat props format.** All scene properties go at the TOP LEVEL of the cut object
(e.g., `cut.text`, `cut.chartData`), NOT nested under a `props` key.

**3. KPI Grid data rules:**
- `value` must be a small, human-readable number. The component auto-formats ≥1M→"XM", ≥1K→"XK".
  For "8.1 Billion" use `value: 8.1, suffix: " Billion"`. Never use raw huge numbers with a suffix.
