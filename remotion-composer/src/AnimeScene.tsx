// Copiado desde OpenMontage-main
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { ParticleOverlay, type ParticleType } from "./ParticleOverlay";

function resolveAsset(src: string): string {
  if (
    src.startsWith("http://") ||
    src.startsWith("https://") ||
    src.startsWith("data:")
  ) {
    return src;
  }
  const clean = src.replace(/^file:\/\/\/?/, "");
  return staticFile(clean);
}

export type CameraMotion =
  | "zoom-in"
  | "zoom-out"
  | "pan-left"
  | "pan-right"
  | "ken-burns"
  | "drift-up"
  | "drift-down"
  | "parallax"
  | "static";

export interface AnimeSceneProps {
  images: string[];
  animation?: CameraMotion;
  particles?: ParticleType;
  particleColor?: string;
  particleCount?: number;
  particleIntensity?: number;
  backgroundColor?: string;
  vignette?: boolean;
  lightingFrom?: string;
  lightingTo?: string;
  sceneDurationSeconds?: number;
}

const AnimeVignette: React.FC = () => (
  <AbsoluteFill
    style={{
      background:
        "radial-gradient(ellipse at center, transparent 35%, rgba(0,0,0,0.6) 100%)",
      pointerEvents: "none",
    }}
  />
);

function useCameraMotion(animation: CameraMotion, effectiveDuration: number) {
  const frame = useCurrentFrame();

  const progress = interpolate(frame, [0, effectiveDuration], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  let scale = 1;
  let translateX = 0;
  let translateY = 0;

  switch (animation) {
    case "zoom-in":
      scale = 1 + progress * 0.15;
      break;
    case "zoom-out":
      scale = 1.15 - progress * 0.15;
      break;
    case "pan-left":
      translateX = interpolate(progress, [0, 1], [35, -35]);
      scale = 1.12;
      break;
    case "pan-right":
      translateX = interpolate(progress, [0, 1], [-35, 35]);
      scale = 1.12;
      break;
    case "ken-burns":
      scale = 1 + progress * 0.18;
      translateX = interpolate(progress, [0, 1], [0, -22]);
      translateY = interpolate(progress, [0, 1], [0, -14]);
      break;
    case "drift-up":
      translateY = interpolate(progress, [0, 1], [22, -22]);
      scale = 1.1;
      break;
    case "drift-down":
      translateY = interpolate(progress, [0, 1], [-22, 22]);
      scale = 1.1;
      break;
    case "parallax":
      translateY = interpolate(progress, [0, 1], [14, -14]);
      translateX = interpolate(progress, [0, 1], [6, -6]);
      scale = 1.12;
      break;
    case "static":
    default:
      scale = 1.02;
      break;
  }

  return { scale, translateX, translateY };
}

export const AnimeScene: React.FC<AnimeSceneProps> = ({
  images,
  animation = "ken-burns",
  particles,
  particleColor = "#FFE082",
  particleCount = 20,
  particleIntensity = 0.6,
  backgroundColor = "#0A0A1A",
  vignette = true,
  lightingFrom,
  lightingTo,
  sceneDurationSeconds,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const effectiveDuration = sceneDurationSeconds
    ? Math.round(sceneDurationSeconds * fps)
    : durationInFrames;
  const { scale, translateX, translateY } = useCameraMotion(
    animation,
    effectiveDuration
  );
  const imageCount = images.length;
  const crossfadeDur = Math.round(fps * 1.2);
  const getOpacity = (idx: number): number => {
    const sceneIn = spring({
      frame,
      fps,
      config: { damping: 18, stiffness: 80 },
    });
    const sceneOut = interpolate(
      frame,
      [effectiveDuration - 10, effectiveDuration],
      [1, 0.25],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
    );
    if (imageCount <= 1) {
      return sceneIn * sceneOut;
    }
    const segmentDur = effectiveDuration / imageCount;
    const segStart = idx * segmentDur;
    const segEnd = segStart + segmentDur;
    const fadeIn =
      idx === 0
        ? sceneIn
        : interpolate(
            frame,
            [segStart - crossfadeDur, segStart],
            [0, 1],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
          );
    const fadeOut =
      idx === imageCount - 1
        ? sceneOut
        : interpolate(
            frame,
            [segEnd - crossfadeDur, segEnd],
            [1, 0],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
          );
    return Math.max(0, Math.min(1, fadeIn * fadeOut));
  };
  const lightProgress = interpolate(frame, [0, effectiveDuration], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const lightOpacity =
    lightingFrom && lightingTo
      ? interpolate(lightProgress, [0, 0.3, 0.7, 1], [0, 0.25, 0.25, 0.1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 0;
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: backgroundColor }}>
      {images.map((src, i) => (
        <AbsoluteFill key={i}>
          <Img
            src={resolveAsset(src)}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              opacity: getOpacity(i),
              transform: `scale(${scale}) translate(${translateX}px, ${translateY}px)`,
              willChange: "transform, opacity",
            }}
          />
        </AbsoluteFill>
      ))}
      {lightingFrom && lightingTo && (
        <AbsoluteFill
          style={{
            background: `linear-gradient(135deg, ${lightingFrom}, ${lightingTo})`,
            opacity: lightOpacity,
            pointerEvents: "none",
          }}
        />
      )}
      {vignette && <AnimeVignette />}
      {particles && (
        <ParticleOverlay
          type={particles}
          count={particleCount}
          color={particleColor}
          intensity={particleIntensity}
        />
      )}
    </AbsoluteFill>
  );
};