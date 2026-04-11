export type DirectorTheme = "cyberpunk" | "minimal" | "playful";

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
}