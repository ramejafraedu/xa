import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { CinematicImageScene, CinematicTone } from "../cinematic/types";
import { ParticleOverlay } from "./ParticleOverlay"; // Let's reuse or create

// Assume resolveAsset is available or we define it:
function resolveAsset(src: string): string {
  if (!src) return src;
  if (src.startsWith("http://") || src.startsWith("https://") || src.startsWith("data:")) return src;
  const normalized = src.replace(/\\/g, "/");
  if (normalized.startsWith("workspace/")) return `/${normalized}`; // Or however Remotion handles it locally
  if (src.startsWith("file://")) {
    let clean = src.replace(/\\/g, "/");
    try { clean = decodeURI(clean); } catch (e) {}
    return clean;
  }
  if (/^[A-Za-z]:\//.test(normalized) || normalized.startsWith("/")) {
    const path = normalized.startsWith("/") ? normalized : `/${normalized}`;
    return `file://${encodeURI(path)}`;
  }
  return `/${normalized}`;
}

const toneGradient = (tone: CinematicTone) => {
  switch (tone) {
    case "steel":
      return "linear-gradient(180deg, rgba(6,12,18,0.18) 0%, rgba(2,4,8,0.48) 100%)";
    case "void":
      return "linear-gradient(180deg, rgba(2,4,8,0.14) 0%, rgba(0,0,0,0.56) 100%)";
    case "neutral":
      return "linear-gradient(180deg, rgba(10,10,12,0.16) 0%, rgba(0,0,0,0.42) 100%)";
    case "cold":
    default:
      return "linear-gradient(180deg, rgba(8,16,24,0.18) 0%, rgba(2,4,8,0.42) 100%)";
  }
};

export const ImageScene: React.FC<{ scene: CinematicImageScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const fadeInFrames = scene.fadeInFrames ?? 10;
  const fadeOutFrames = scene.fadeOutFrames ?? 10;
  const fadeOutStart = Math.max(fadeInFrames, durationInFrames - fadeOutFrames);
  
  const fadeInOpacity = fadeInFrames === 0 ? 1 : interpolate(frame, [0, fadeInFrames], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp"
  });
  const fadeOutOpacity = fadeOutFrames === 0 ? 1 : interpolate(frame, [fadeOutStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp"
  });
  const opacity = Math.min(fadeInOpacity, fadeOutOpacity);

  const animation = scene.animation ?? "kenBurns";
  const intensity = scene.animationIntensity ?? 1.0;
  
  let scale = 1;
  let translateX = 0;
  let translateY = 0;

  if (animation === "kenBurns") {
    // Slow zoom in
    const maxScale = 1 + (0.15 * intensity);
    scale = interpolate(frame, [0, durationInFrames], [1.0, maxScale], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp"
    });
    
    // Slow pan depending on direction
    const panAmount = 40 * intensity;
    if (scene.animationDirection === "left") translateX = interpolate(frame, [0, durationInFrames], [0, -panAmount]);
    if (scene.animationDirection === "right") translateX = interpolate(frame, [0, durationInFrames], [0, panAmount]);
    if (scene.animationDirection === "up") translateY = interpolate(frame, [0, durationInFrames], [0, -panAmount]);
    if (scene.animationDirection === "down") translateY = interpolate(frame, [0, durationInFrames], [0, panAmount]);
  } 
  else if (animation === "parallax") {
    // We simulate parallax by scaling up more and moving
    const maxScale = 1.15;
    scale = interpolate(frame, [0, durationInFrames], [maxScale, 1.0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp"
    });
  }
  else if (animation === "panCross") {
    scale = 1.15;
    const panAmount = 80 * intensity;
    translateX = interpolate(frame, [0, durationInFrames], [panAmount, -panAmount]);
  }
  else if (animation === "zoomPulse") {
    // Pulse on beats (assuming 120bpm = 1 beat every 0.5s = 15 frames)
    const beat = Math.sin((frame / 15) * Math.PI);
    scale = 1.05 + (beat * 0.05 * intensity);
  } else {
    // Default fallback
    scale = interpolate(frame, [0, durationInFrames], [1.015, 1], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp"
    });
  }

  // Ensure particle overlay is safe
  const showParticles = scene.overlayParticles ?? false;

  return (
    <AbsoluteFill style={{ backgroundColor: "#020407", opacity }}>
      {/* The main image */}
      <AbsoluteFill style={{
          transform: `scale(${scale}) translateX(${translateX}px) translateY(${translateY}px)`,
          filter: scene.filter ?? "contrast(1.05) saturate(1.1) brightness(0.95)",
          justifyContent: "center",
          alignItems: "center"
      }}>
        <Img 
          src={resolveAsset(scene.src)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </AbsoluteFill>

      {/* Cinematic Overlays (same as video) */}
      <AbsoluteFill
        style={{
          background: toneGradient(scene.tone ?? "cold"),
          mixBlendMode: "multiply",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at center, transparent 40%, rgba(0,0,0,0.6) 100%)",
        }}
      />
      
      {/* Optional Particles */}
      {showParticles ? (
        <ParticleOverlay type="sparkles" count={14} intensity={0.35} />
      ) : null}

      {/* Film grain effect */}
      <AbsoluteFill
        style={{
          background: "linear-gradient(180deg, rgba(255,255,255,0.03) 0%, transparent 8%, transparent 92%, rgba(255,255,255,0.03) 100%)",
          opacity: 0.6,
        }}
      />
    </AbsoluteFill>
  );
};
