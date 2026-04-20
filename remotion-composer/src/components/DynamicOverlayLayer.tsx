import React from "react";
import {
  AbsoluteFill,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { DynamicOverlay, DynamicOverlayPosition } from "../cinematic/types";
import { KineticText } from "./components/KineticText";

const FPS_FALLBACK = 30;

const positionStyle = (
  position: DynamicOverlayPosition | undefined
): React.CSSProperties => {
  switch (position) {
    case "top":
    case "top_third":
      return { justifyContent: "flex-start", paddingTop: "12%" };
    case "bottom":
    case "bottom_third":
      return { justifyContent: "flex-end", paddingBottom: "14%" };
    case "left":
      return { justifyContent: "center", alignItems: "flex-start", paddingLeft: "6%" };
    case "right":
      return { justifyContent: "center", alignItems: "flex-end", paddingRight: "6%" };
    case "center":
    default:
      return { justifyContent: "center" };
  }
};

const FlashPop: React.FC<{ style?: string }> = ({ style }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const opacity = interpolate(
    frame,
    [0, durationInFrames * 0.4, durationInFrames],
    [0, 0.85, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  const color = style === "white_flash" ? "#ffffff" : "#fde68a";
  return <AbsoluteFill style={{ backgroundColor: color, opacity, mixBlendMode: "screen" }} />;
};

const LowerThird: React.FC<{ text: string; style?: string }> = ({ text, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ fps, frame, config: { damping: 18, stiffness: 110 } });
  const y = interpolate(enter, [0, 1], [80, 0]);
  const opacity = interpolate(enter, [0, 1], [0, 1]);
  const bg =
    style === "gold"
      ? "linear-gradient(90deg, #f5c451 0%, #c88a1a 100%)"
      : "linear-gradient(90deg, rgba(15,23,42,0.92) 0%, rgba(30,41,59,0.92) 100%)";
  const textColor = style === "gold" ? "#0B1324" : "#F8FAFC";
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", paddingBottom: "10%" }}>
      <div
        style={{
          margin: "0 6%",
          padding: "18px 28px",
          borderRadius: 18,
          background: bg,
          opacity,
          transform: `translateY(${y}px)`,
          boxShadow: "0 12px 32px rgba(0,0,0,0.35)",
          color: textColor,
          fontWeight: 800,
          fontSize: 48,
          letterSpacing: 0.2,
          textAlign: "center",
          fontFamily: "Inter, system-ui, sans-serif",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const KineticHook: React.FC<{ text: string; position?: DynamicOverlayPosition }> = ({
  text,
  position,
}) => {
  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        padding: "0 6%",
        ...positionStyle(position),
      }}
    >
      <KineticText
        text={text}
        color="#FDE68A"
        style={{
          fontSize: 84,
          textAlign: "center",
          textShadow: "0 4px 24px rgba(0,0,0,0.6)",
        }}
      />
    </AbsoluteFill>
  );
};

const AmbientLines: React.FC<{ accent: string }> = ({ accent }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {Array.from({ length: 12 }).map((_, i) => {
        const pulse = Math.max(0, Math.sin(frame * 0.08 + i));
        const opacity = 0.05 + pulse * 0.12;
        const top = 120 + i * 80;
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              top,
              left: i % 2 === 0 ? 0 : "60%",
              width: "40%",
              height: 2,
              background: accent,
              boxShadow: `0 0 16px ${accent}`,
              opacity,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

const MoneyRain: React.FC = () => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {Array.from({ length: 14 }).map((_, i) => {
        const delay = i * 4;
        const t = (frame - delay) / durationInFrames;
        if (t < 0 || t > 1) return null;
        const y = interpolate(t, [0, 1], [-120, 1400]);
        const x = 40 + i * 72 + Math.sin(frame * 0.05 + i) * 20;
        const opacity = interpolate(t, [0, 0.15, 0.85, 1], [0, 1, 1, 0]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              transform: `translate(${x}px, ${y}px) rotate(${i * 17}deg)`,
              fontSize: 56,
              opacity,
            }}
          >
            💰
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

const QuestionBurst: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const scale = spring({ fps, frame, config: { damping: 10, stiffness: 200 } });
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          transform: `scale(${scale})`,
          fontSize: 220,
          color: "#fde68a",
          textShadow: "0 0 32px rgba(253,230,138,0.55)",
          fontWeight: 900,
        }}
      >
        ?
      </div>
    </AbsoluteFill>
  );
};

const IconBadge: React.FC<{ emoji: string; accent?: string }> = ({ emoji, accent = "#fde68a" }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ fps, frame, config: { damping: 16, stiffness: 120 } });
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div
        style={{
          transform: `scale(${enter})`,
          fontSize: 180,
          filter: `drop-shadow(0 0 28px ${accent})`,
        }}
      >
        {emoji}
      </div>
    </AbsoluteFill>
  );
};

const CaptionBanner: React.FC<{ text: string; position?: DynamicOverlayPosition }> = ({
  text,
  position,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ fps, frame, config: { damping: 18, stiffness: 140 } });
  const opacity = interpolate(enter, [0, 1], [0, 1]);
  return (
    <AbsoluteFill style={{ padding: "0 6%", ...positionStyle(position ?? "bottom_third") }}>
      <div
        style={{
          margin: "0 auto",
          padding: "14px 24px",
          borderRadius: 16,
          backgroundColor: "rgba(0,0,0,0.55)",
          color: "#F8FAFC",
          opacity,
          fontWeight: 700,
          fontSize: 44,
          letterSpacing: 0.3,
          textAlign: "center",
          fontFamily: "Inter, system-ui, sans-serif",
          maxWidth: "90%",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const renderOverlay = (overlay: DynamicOverlay): React.ReactNode => {
  const text = (overlay.text ?? "").trim();
  switch (overlay.type) {
    case "hook_kinetic":
      if (!text) return null;
      return <KineticHook text={text} position={overlay.position} />;
    case "lower_third":
      if (!text) return null;
      return <LowerThird text={text} style={overlay.style} />;
    case "money_rain":
      return <MoneyRain />;
    case "question_burst":
      return <QuestionBurst />;
    case "lightbulb":
      return <IconBadge emoji="💡" accent="#fde68a" />;
    case "tech_lines":
      return <AmbientLines accent="#22d3ee" />;
    case "old_timeline":
      return <AmbientLines accent="#c58e3a" />;
    case "flash_pop":
      return <FlashPop style={overlay.style} />;
    case "caption":
      if (!text) return null;
      return <CaptionBanner text={text} position={overlay.position} />;
    default:
      if (!text) return null;
      return <CaptionBanner text={text} position={overlay.position} />;
  }
};

export const DynamicOverlayLayer: React.FC<{
  overlays?: DynamicOverlay[];
  fps?: number;
}> = ({ overlays, fps }) => {
  if (!overlays || overlays.length === 0) return null;
  const frameRate = fps ?? FPS_FALLBACK;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {overlays.map((overlay, idx) => {
        const from = Math.max(0, Math.round(overlay.startSeconds * frameRate));
        const durationInFrames = Math.max(
          1,
          Math.round(overlay.durationSeconds * frameRate)
        );
        const node = renderOverlay(overlay);
        if (!node) return null;
        return (
          <Sequence
            key={`overlay_${idx}_${overlay.type}`}
            from={from}
            durationInFrames={durationInFrames}
            layout="none"
          >
            {node}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
