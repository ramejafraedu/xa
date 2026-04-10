import { Config } from "@remotion/cli/config";

Config.setConcurrency(Math.max(1, require('os').cpus().length));
Config.setVideoBitrate("8000k");
Config.setAudioBitrate("192k");

/**
 * Remotion Configuration - V16 PRO Enterprise
 * Forzar Remotion como provider principal de renderizado
 */

export const config: Config = {
  // Forzar Remotion como renderer principal (sin fallback a FFmpeg)
  ffmpegPath: undefined, // Usar FFmpeg interno de Remotion
  ffprobePath: undefined,
  
  // Configuración de calidad para videos 9:16 (TikTok/Shorts)
  preset: "ultrafast", // Balance velocidad/calidad para servidor 8GB
  videoCodec: "h264",
  audioCodec: "aac",
  
  // Dimensiones 9:16
  width: 1080,
  height: 1920,
  fps: 30,
  
  // Calidad
  jpegQuality: 90,
  quality: 80,
  
  // Timeout para renders largos (5 minutos máximo por video)
  timeoutInMilliseconds: 300000,
  
  // Concurrency - optimizado para n2-standard-8 (8 vCPU)
  // Usar 4 workers para no saturar la RAM
  concurrency: 4,
  
  // Logging
  logLevel: "verbose",
  
  // No usar caché entre renders (forzar variedad)
  deleteAfter: "never",
  
  // Webpack
  webpackOverride: (config) => config,
};

export default config;
