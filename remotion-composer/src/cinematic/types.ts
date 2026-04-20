export type CinematicTone = "cold" | "steel" | "void" | "neutral";

export interface CinematicBaseScene {
  id: string;
  startSeconds: number;
  durationSeconds: number;
}

export interface CinematicVideoScene extends CinematicBaseScene {
  kind: "video";
  src: string;
  tone?: CinematicTone;
  trimBeforeSeconds?: number;
  trimAfterSeconds?: number;
  filter?: string;
  fadeInFrames?: number;
  fadeOutFrames?: number;
}

export interface CinematicTitleScene extends CinematicBaseScene {
  kind: "title";
  text: string;
  accent?: string;
  intensity?: number;
}

export interface CinematicImageScene extends CinematicBaseScene {
  kind: "image";
  src: string;
  animation?: "kenBurns" | "parallax" | "panCross" | "zoomPulse";
  animationIntensity?: number;
  animationDirection?: "left" | "right" | "up" | "down" | "center";
  overlayParticles?: boolean;
  tone?: CinematicTone;
  filter?: string;
  fadeInFrames?: number;
  fadeOutFrames?: number;
}

export type CinematicScene = CinematicVideoScene | CinematicTitleScene | CinematicImageScene;

export interface CinematicSoundtrack {
  src: string;
  volume?: number;
  trimBeforeSeconds?: number;
  trimAfterSeconds?: number;
  fadeInSeconds?: number;
  fadeOutSeconds?: number;
}

export interface CinematicWordCaption {
  word: string;
  startMs: number;
  endMs: number;
}

export interface CinematicCaptionConfig {
  words: CinematicWordCaption[];
  wordsPerPage?: number;
  fontSize?: number;
  color?: string;
  highlightColor?: string;
  backgroundColor?: string;
}

export type DynamicOverlayType =
  | "hook_kinetic"
  | "lower_third"
  | "money_rain"
  | "question_burst"
  | "lightbulb"
  | "old_timeline"
  | "tech_lines"
  | "flash_pop"
  | "caption"
  | string;

export type DynamicOverlayPosition =
  | "top"
  | "center"
  | "bottom"
  | "bottom_third"
  | "top_third"
  | "left"
  | "right"
  | string;

export interface DynamicOverlay {
  type: DynamicOverlayType;
  startSeconds: number;
  durationSeconds: number;
  text?: string;
  style?: string;
  position?: DynamicOverlayPosition;
}

export interface CinematicRendererProps {
  [key: string]: unknown;
  scenes: CinematicScene[];
  audioDurationInSeconds?: number;
  titleFontSize?: number;
  titleWidth?: number;
  signalLineCount?: number;
  soundtrack?: CinematicSoundtrack;
  music?: CinematicSoundtrack;
  captions?: CinematicCaptionConfig;
  dynamicOverlays?: DynamicOverlay[];
  /**
   * Short-form indicator: "9:16" renders 1080x1920 by default, "16:9" keeps the
   * legacy 1920x1080 output. Ignored when `resolution` is provided explicitly.
   */
  format?: "9:16" | "16:9" | string;
  resolution?: { width: number; height: number };
}
