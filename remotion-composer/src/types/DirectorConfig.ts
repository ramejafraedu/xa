export type DirectorTheme =
  | "clean-professional"
  | "flat-motion-graphics"
  | "minimalist-diagram"
  | "anime-ghibli"
  | "cyberpunk"
  | "minimal"
  | "playful"
  | string;

export interface DirectorThemeConfig {
  primaryColor?: string;
  accentColor?: string;
  headingFont?: string;
  bodyFont?: string;
  fontFamily?: string;
  captionHighlightColor?: string;
  captionBackgroundColor?: string;
  transitionDuration?: number;
}

export interface DirectorProjectInfo {
  name: string;
  tagline?: string;
  website?: string;
  industry?: string;
}

export interface DirectorStyle {
  theme: DirectorTheme;
  primaryColor: string;
  accentColor: string;
  fontFamily?: string;
  layoutVariant?: "split" | "stacked" | "spotlight";
  kineticLevel?: "soft" | "dynamic" | "intense";
  transitionPreset?: "slide" | "swipe" | "pulse";
  featureCardMode?: "window" | "plain";
}

export interface DirectorFeature {
  title: string;
  subtitle: string;
  imagePath?: string;
}

export interface DirectorScript {
  hook: string;
  solution: string;
  features: DirectorFeature[];
  cta: string;
}

export interface DirectorAudio {
  bgmPath?: string;
  volume?: number;
}

export interface DirectorConfig {
  projectInfo: DirectorProjectInfo;
  style: DirectorStyle;
  script: DirectorScript;
  audio?: DirectorAudio;
  theme?: DirectorTheme;
  playbook?: string;
  themeConfig?: DirectorThemeConfig;
  layoutVariant?: "split" | "stacked" | "spotlight";
  kineticLevel?: "soft" | "dynamic" | "intense";
  transitionPreset?: "slide" | "swipe" | "pulse";
  featureCardMode?: "window" | "plain";
}