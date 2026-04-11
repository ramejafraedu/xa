import { Composition, CalculateMetadataFunction } from "remotion";
import { Explainer, ExplainerProps } from "./Explainer";
import {
  CinematicRenderer,
  calculateCinematicMetadata,
} from "./CinematicRenderer";
import { signalFromTomorrowWithMusicFixture } from "./cinematic/fixtures";
import { TalkingHead, TalkingHeadProps } from "./TalkingHead";
import { UniversalCommercial } from "./templates/UniversalCommercial";
import { DirectorConfig } from "./types/DirectorConfig";

// ---------------------------------------------------------------------------
// Theme System — prevents every video from looking like dark fintech
// ---------------------------------------------------------------------------

export interface ThemeConfig {
  primaryColor: string;
  accentColor: string;
  backgroundColor: string;
  surfaceColor: string;
  textColor: string;
  mutedTextColor: string;
  headingFont: string;
  bodyFont: string;
  monoFont: string;
  chartColors: string[];
  springConfig: { damping: number; stiffness: number; mass: number };
  transitionDuration: number;
  captionHighlightColor: string;
  captionBackgroundColor: string;
}

export const THEMES: Record<string, ThemeConfig> = {
  "clean-professional": {
    primaryColor: "#2563EB",
    accentColor: "#F59E0B",
    backgroundColor: "#FFFFFF",
    surfaceColor: "#F9FAFB",
    textColor: "#1F2937",
    mutedTextColor: "#6B7280",
    headingFont: "Inter",
    bodyFont: "Inter",
    monoFont: "JetBrains Mono",
    chartColors: ["#2563EB", "#F59E0B", "#10B981", "#8B5CF6", "#EC4899", "#06B6D4"],
    springConfig: { damping: 20, stiffness: 120, mass: 1 },
    transitionDuration: 0.4,
    captionHighlightColor: "#2563EB",
    captionBackgroundColor: "rgba(255, 255, 255, 0.85)",
  },
  "flat-motion-graphics": {
    primaryColor: "#7C3AED",
    accentColor: "#EC4899",
    backgroundColor: "#0F172A",
    surfaceColor: "#1E293B",
    textColor: "#F8FAFC",
    mutedTextColor: "#94A3B8",
    headingFont: "Space Grotesk",
    bodyFont: "Space Grotesk",
    monoFont: "Fira Code",
    chartColors: ["#7C3AED", "#EC4899", "#06B6D4", "#F59E0B", "#10B981", "#EF4444"],
    springConfig: { damping: 12, stiffness: 80, mass: 1 },
    transitionDuration: 0.3,
    captionHighlightColor: "#22D3EE",
    captionBackgroundColor: "rgba(15, 23, 42, 0.75)",
  },
  "minimalist-diagram": {
    primaryColor: "#1A1A2E",
    accentColor: "#E94560",
    backgroundColor: "#FAFAFA",
    surfaceColor: "#FFFFFF",
    textColor: "#1A1A2E",
    mutedTextColor: "#6B7280",
    headingFont: "IBM Plex Sans",
    bodyFont: "IBM Plex Sans",
    monoFont: "IBM Plex Mono",
    chartColors: ["#E94560", "#1A1A2E", "#0F3460", "#9CA3AF"],
    springConfig: { damping: 25, stiffness: 150, mass: 1 },
    transitionDuration: 0.5,
    captionHighlightColor: "#E94560",
    captionBackgroundColor: "rgba(250, 250, 250, 0.9)",
  },
  "anime-ghibli": {
    primaryColor: "#2D5016",
    accentColor: "#FFB347",
    backgroundColor: "#0A0A1A",
    surfaceColor: "#1A2332",
    textColor: "#F0E6D3",
    mutedTextColor: "#A8957E",
    headingFont: "Noto Serif JP",
    bodyFont: "Noto Sans",
    monoFont: "Fira Code",
    chartColors: ["#FFB347", "#2D5016", "#FF6B9D", "#A8E6CF", "#6B4C8A", "#E8927C"],
    springConfig: { damping: 18, stiffness: 60, mass: 1 },
    transitionDuration: 1.0,
    captionHighlightColor: "#FFB347",
    captionBackgroundColor: "rgba(10, 10, 26, 0.8)",
  },
  cyberpunk: {
    primaryColor: "#0F172A",
    accentColor: "#22D3EE",
    backgroundColor: "#020617",
    surfaceColor: "#111827",
    textColor: "#E2E8F0",
    mutedTextColor: "#94A3B8",
    headingFont: "Orbitron",
    bodyFont: "Space Grotesk",
    monoFont: "JetBrains Mono",
    chartColors: ["#22D3EE", "#A78BFA", "#F43F5E", "#F59E0B", "#34D399"],
    springConfig: { damping: 13, stiffness: 95, mass: 1 },
    transitionDuration: 0.3,
    captionHighlightColor: "#22D3EE",
    captionBackgroundColor: "rgba(2, 6, 23, 0.78)",
  },
  minimal: {
    primaryColor: "#334155",
    accentColor: "#F97316",
    backgroundColor: "#F8FAFC",
    surfaceColor: "#FFFFFF",
    textColor: "#0F172A",
    mutedTextColor: "#64748B",
    headingFont: "IBM Plex Sans",
    bodyFont: "IBM Plex Sans",
    monoFont: "IBM Plex Mono",
    chartColors: ["#334155", "#F97316", "#0EA5E9", "#84CC16", "#A855F7"],
    springConfig: { damping: 24, stiffness: 150, mass: 1 },
    transitionDuration: 0.45,
    captionHighlightColor: "#F97316",
    captionBackgroundColor: "rgba(248, 250, 252, 0.88)",
  },
  playful: {
    primaryColor: "#BE123C",
    accentColor: "#14B8A6",
    backgroundColor: "#FFF1F2",
    surfaceColor: "#FFE4E6",
    textColor: "#9F1239",
    mutedTextColor: "#9D174D",
    headingFont: "Baloo 2",
    bodyFont: "Nunito",
    monoFont: "Fira Code",
    chartColors: ["#BE123C", "#14B8A6", "#F59E0B", "#8B5CF6", "#22C55E"],
    springConfig: { damping: 16, stiffness: 90, mass: 1 },
    transitionDuration: 0.35,
    captionHighlightColor: "#14B8A6",
    captionBackgroundColor: "rgba(255, 228, 230, 0.88)",
  },
};

// Default theme when none is specified — uses the existing dark style for backwards compatibility
export const DEFAULT_THEME = THEMES["flat-motion-graphics"];

export function resolveTheme(props: Record<string, unknown>): ThemeConfig {
  const themeName = (props.theme as string) || (props.playbook as string);
  if (themeName && THEMES[themeName]) {
    return THEMES[themeName];
  }
  // Allow custom theme passed as full object
  if (props.themeConfig && typeof props.themeConfig === "object") {
    return { ...DEFAULT_THEME, ...(props.themeConfig as Partial<ThemeConfig>) };
  }
  return DEFAULT_THEME;
}

const calculateMetadata: CalculateMetadataFunction<ExplainerProps> = async ({
  props,
}) => {
  const cuts = props.cuts || [];
  if (cuts.length === 0) {
    return { durationInFrames: 30 * 60 };
  }
  const lastEnd = Math.max(...cuts.map((c) => c.out_seconds || 0));
  // Add 1 second padding for final fade
  return { durationInFrames: Math.ceil((lastEnd + 1) * 30) };
};

const calculateUniversalCommercialMetadata: CalculateMetadataFunction<DirectorConfig> = async ({
  props,
}) => {
  const hookDuration = 150;
  const solutionDuration = 150;
  const featureDuration = 180;
  const ctaDuration = 150;
  const transitionDuration = 30;

  const featureCount = Math.max(0, props?.script?.features?.length ?? 0);
  const transitionCount = featureCount > 0 ? featureCount + 2 : 3;
  const totalFrames =
    hookDuration +
    solutionDuration +
    ctaDuration +
    (featureCount * featureDuration) +
    (transitionCount * transitionDuration);

  return { durationInFrames: Math.max(totalFrames, 30 * 12) };
};

const defaultUniversalCommercialProps: DirectorConfig = {
  projectInfo: {
    name: "Video Factory",
    tagline: "Narrativas que convierten",
  },
  style: {
    theme: "minimal",
    primaryColor: "#334155",
    accentColor: "#F97316",
    fontFamily: "Space Grotesk, sans-serif",
  },
  script: {
    hook: "Haz que cada segundo venda tu idea.",
    solution: "Creamos videos comerciales con ritmo, claridad y estilo visual consistente.",
    features: [
      {
        title: "Gancho inmediato",
        subtitle: "Primeros 3 segundos orientados a retencion",
      },
      {
        title: "Visuales de marca",
        subtitle: "Paleta, tipografia y tono narrativo alineados",
      },
      {
        title: "CTA accionable",
        subtitle: "Cierre directo para clicks, leads o ventas",
      },
    ],
    cta: "Convierte hoy",
  },
  audio: {
    volume: 0.4,
  },
};

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="Explainer"
        component={Explainer}
        durationInFrames={30 * 60}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          cuts: [],
          overlays: [],
          captions: [],
          audio: {},
        }}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="CinematicRenderer"
        component={CinematicRenderer}
        durationInFrames={30 * 60}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          scenes: [],
        }}
        schema={null as any}
        calculateMetadata={calculateCinematicMetadata}
      />
      <Composition
        id="SignalFromTomorrowWithMusic"
        component={CinematicRenderer}
        durationInFrames={30 * 30}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={signalFromTomorrowWithMusicFixture}
        calculateMetadata={calculateCinematicMetadata}
      />
      <Composition
        id="TalkingHead"
        component={TalkingHead}
        durationInFrames={30 * 300}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          videoSrc: "",
          captions: [],
          overlays: [],
          wordsPerPage: 4,
          fontSize: 52,
          highlightColor: "#22D3EE",
        }}
      />
      <Composition
        id="UniversalCommercial"
        component={UniversalCommercial}
        durationInFrames={30 * 60}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={defaultUniversalCommercialProps}
        calculateMetadata={calculateUniversalCommercialMetadata}
      />
    </>
  );
};
