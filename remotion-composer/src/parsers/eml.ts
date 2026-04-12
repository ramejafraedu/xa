/**
 * EML (Editing Markup Language) Parser
 * Integración de ShortGPT para sincronía perfecta subtítulos-audio-video
 */

export interface EMLScene {
  id: string;
  start: number;        // Start frame
  duration: number;     // Duration in frames
  video?: string;       // Video clip path
  audio?: string;       // Audio segment path
  subtitle: {
    text: string;
    start: number;
    end: number;
    words: EMLWord[];
  };
  visualCues?: string[];
  transition?: string;
}

export interface EMLWord {
  word: string;
  start: number;        // Start time in seconds
  end: number;          // End time in seconds
  confidence?: number;
}

export interface EMLManifest {
  version: string;
  duration: number;     // Total duration in seconds
  fps: number;
  scenes: EMLScene[];
  audioTrack?: {
    path: string;
    duration: number;
  };
  globalStyle?: {
    theme?: string;
    primaryColor?: string;
    accentColor?: string;
  };
}

/**
 * Parse EML JSON into structured scenes for Remotion
 */
export function parseEML(emlData: string | object): EMLManifest {
  const data = typeof emlData === 'string' ? JSON.parse(emlData) : emlData;
  
  // Validate required fields
  if (!data.scenes || !Array.isArray(data.scenes)) {
    throw new Error('EML manifest must have a scenes array');
  }
  
  const manifest: EMLManifest = {
    version: data.version || '1.0',
    duration: data.duration || 0,
    fps: data.fps || 30,
    scenes: data.scenes.map((scene: any, index: number) => parseScene(scene, index)),
    audioTrack: data.audioTrack,
    globalStyle: data.globalStyle || {},
  };
  
  return manifest;
}

function parseScene(sceneData: any, index: number): EMLScene {
  const fps = sceneData.fps || 30;
  
  return {
    id: sceneData.id || `scene_${index}`,
    start: Math.floor((sceneData.startTime || 0) * fps),
    duration: Math.floor((sceneData.duration || 5) * fps),
    video: sceneData.video,
    audio: sceneData.audio,
    subtitle: parseSubtitle(sceneData.subtitle, fps),
    visualCues: sceneData.visualCues || [],
    transition: sceneData.transition || 'fade',
  };
}

function parseSubtitle(subData: any, fps: number): EMLScene['subtitle'] {
  if (!subData) {
    return {
      text: '',
      start: 0,
      end: 0,
      words: [],
    };
  }
  
  return {
    text: subData.text || '',
    start: Math.floor((subData.startTime || 0) * fps),
    end: Math.floor((subData.endTime || 0) * fps),
    words: (subData.words || []).map((word: any) => ({
      word: word.word || word.text || '',
      start: word.startTime || word.start || 0,
      end: word.endTime || word.end || 0,
      confidence: word.confidence,
    })),
  };
}

/**
 * Calculate scene timing for synchronized subtitles
 */
export function calculateWordTiming(
  words: EMLWord[],
  sceneStartFrame: number,
  fps: number
): { word: string; frame: number; duration: number }[] {
  return words.map((word) => {
    const frame = Math.floor(word.start * fps) - sceneStartFrame;
    const duration = Math.ceil((word.end - word.start) * fps);
    return {
      word: word.word,
      frame: Math.max(0, frame),
      duration: Math.max(1, duration),
    };
  });
}

/**
 * Generate default EML structure from script
 */
export function generateEMLFromScript(
  script: string,
  audioDuration: number,
  fps: number = 30
): EMLManifest {
  const sentences = script.split(/[.!?]+/).filter(s => s.trim().length > 0);
  const sentenceDuration = audioDuration / sentences.length;
  
  const scenes: EMLScene[] = sentences.map((sentence, index) => {
    const startTime = index * sentenceDuration;
    const words = sentence.trim().split(/\s+/).map((word, wordIndex) => ({
      word,
      start: startTime + (wordIndex * (sentenceDuration / (sentence.split(/\s+/).length))),
      end: startTime + ((wordIndex + 1) * (sentenceDuration / (sentence.split(/\s+/).length))),
    }));
    
    return {
      id: `scene_${index}`,
      start: Math.floor(startTime * fps),
      duration: Math.floor(sentenceDuration * fps),
      subtitle: {
        text: sentence.trim(),
        start: Math.floor(startTime * fps),
        end: Math.floor((startTime + sentenceDuration) * fps),
        words,
      },
      visualCues: [],
      transition: index === 0 ? 'none' : 'fade',
    };
  });
  
  return {
    version: '1.0',
    duration: audioDuration,
    fps,
    scenes,
    globalStyle: {
      theme: 'cyberpunk',
    },
  };
}

export default { parseEML, calculateWordTiming, generateEMLFromScript };
