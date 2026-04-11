import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, spring, Img, staticFile, Audio as RemotionAudio } from 'remotion';
import { DirectorConfig } from '../types/DirectorConfig';
import { KineticText } from '../components/components/KineticText';
import { MacWindow } from '../components/components/MacWindow';

import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { slide } from "@remotion/transitions/slide";
import { FontLoader } from '../components/components/FontLoader';
import { DynamicBackground } from '../components/components/DynamicBackground';

export const UniversalCommercial: React.FC<DirectorConfig> = ({
    projectInfo,
    style,
    script,
    audio
}) => {
    // Theme Styles
    const getBgColor = () => {
        switch (style.theme) {
            case 'cyberpunk': return '#0f172a';
            case 'minimal': return '#f8f9fa';
            case 'playful': return '#fff1f2';
            default: return style.primaryColor;
        }
    };

    const getTextColor = () => {
        switch (style.theme) {
            case 'cyberpunk': return '#ffffff';
            case 'minimal': return '#333333';
            case 'playful': return '#be123c';
            default: return '#ffffff';
        }
    };

    const bgColor = getBgColor();
    const textColor = getTextColor();
    const accentColor = style.accentColor;
    const fontFamily = style.fontFamily || 'Space Grotesk, sans-serif';

    // Scene Timings
    // Scene Timings
    const HOOK_DURATION = 150; // Increased from 90
    const SOLUTION_DURATION = 150; // Increased from 90
    const FEATURE_DURATION = 180; // Increased from 150
    const CTA_DURATION = 150; // Increased from 120

    return (
        <AbsoluteFill style={{ backgroundColor: bgColor, fontFamily }}>
            {audio?.bgmPath && <RemotionAudio src={staticFile(audio.bgmPath)} volume={audio.volume ?? 0.5} />}
            <FontLoader fontFamily={fontFamily} />
            <DynamicBackground theme={style.theme} primaryColor={style.primaryColor} accentColor={accentColor} />

            <TransitionSeries>
                {/* Scene 1: Hook */}
                <TransitionSeries.Sequence durationInFrames={HOOK_DURATION}>
                    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
                        <KineticText
                            text={script.hook}
                            style={{ fontSize: 80, fontWeight: 'bold' }}
                            color={textColor}
                        />
                        <div style={{ marginTop: 20 }}>
                            <KineticText
                                text={projectInfo.name}
                                delay={10}
                                style={{ fontSize: 40, opacity: 0.8 }}
                                color={accentColor}
                            />
                        </div>
                    </AbsoluteFill>
                </TransitionSeries.Sequence>

                <TransitionSeries.Transition
                    presentation={slide({ direction: "from-right" })}
                    timing={linearTiming({ durationInFrames: 30 })}
                />

                {/* Scene 2: Solution */}
                <TransitionSeries.Sequence durationInFrames={SOLUTION_DURATION}>
                    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
                        <KineticText
                            text={script.solution}
                            style={{ fontSize: 70, fontWeight: 'bold', maxWidth: '80%', textAlign: 'center' }}
                            color={accentColor}
                        />
                    </AbsoluteFill>
                </TransitionSeries.Sequence>

                <TransitionSeries.Transition
                    presentation={slide({ direction: "from-bottom" })}
                    timing={linearTiming({ durationInFrames: 30 })}
                />

                {/* Scene 3: Features */}
                {script.features.map((feature, index) => (
                    <React.Fragment key={index}>
                        <TransitionSeries.Sequence durationInFrames={FEATURE_DURATION}>
                            <FeatureScene
                                feature={feature}
                                textColor={textColor}
                                accentColor={accentColor}
                                fontFamily={fontFamily}
                                theme={style.theme}
                            />
                        </TransitionSeries.Sequence>
                        {index < script.features.length - 1 && (
                            <TransitionSeries.Transition
                                presentation={slide({ direction: "from-right" })}
                                timing={linearTiming({ durationInFrames: 30 })}
                            />
                        )}
                    </React.Fragment>
                ))}

                <TransitionSeries.Transition
                    presentation={slide({ direction: "from-top" })}
                    timing={linearTiming({ durationInFrames: 30 })}
                />

                {/* Scene 4: CTA */}
                <TransitionSeries.Sequence durationInFrames={CTA_DURATION}>
                    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
                        <KineticText
                            text={script.cta}
                            style={{ fontSize: 100, fontWeight: '900' }}
                            color={accentColor}
                        />
                        <div style={{
                            marginTop: 40,
                            padding: '20px 60px',
                            border: `4px solid ${textColor}`,
                            borderRadius: 50,
                            color: textColor,
                            fontSize: 30,
                            fontWeight: 'bold'
                        }}>
                            Get Started
                        </div>
                    </AbsoluteFill>
                </TransitionSeries.Sequence>
            </TransitionSeries>
        </AbsoluteFill>
    );
};

// Sub-component for clean feature display
const FeatureScene: React.FC<{
    feature: { title: string; subtitle: string; imagePath?: string };
    textColor: string;
    accentColor: string;
    fontFamily: string;
    theme: "cyberpunk" | "minimal" | "playful";
}> = ({ feature, textColor, accentColor, fontFamily, theme }) => {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();

    const scale = spring({ frame, fps, config: { damping: 12 } });
    const y = interpolate(frame, [0, 30], [50, 0], { extrapolateRight: 'clamp' });

    // --- Theme Specific Styles ---
    const isCyberpunk = theme === 'cyberpunk';
    const isMinimal = theme === 'minimal';

    // Cyberpunk specific glitch/skew
    const skew = isCyberpunk ? Math.sin(frame / 10) * 5 : 0;
    const opacity = isCyberpunk ? interpolate(frame % 20, [0, 10, 20], [1, 0.8, 1]) : 1;

    // Minimal specific fade
    const minimalOpacity = isMinimal ? interpolate(frame, [0, 20], [0, 1]) : 1;

    return (
        <AbsoluteFill style={{
            justifyContent: 'center',
            alignItems: 'center',
            flexDirection: 'row',
            gap: 60,
            fontFamily,
            opacity: isMinimal ? minimalOpacity : 1
        }}>
            {/* Text Side */}
            <div style={{ flex: 1, paddingLeft: 100, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                <h2 style={{
                    color: accentColor,
                    fontSize: 60,
                    margin: 0,
                    transform: `translateY(${y}px) skewX(${skew}deg)`,
                    textShadow: isCyberpunk ? `2px 2px 0px ${textColor}` : 'none',
                    opacity
                }}>
                    {feature.title}
                </h2>
                <h3 style={{
                    color: textColor,
                    fontSize: 40,
                    margin: '20px 0 0 0',
                    opacity: 0.8,
                    fontWeight: isMinimal ? 300 : 700
                }}>
                    {feature.subtitle}
                </h3>
            </div>

            {/* Image Side */}
            <div style={{ flex: 1.5, display: 'flex', justifyContent: 'center', alignItems: 'center', paddingRight: 100 }}>
                {feature.imagePath ? (
                    <div style={{
                        transform: `scale(${scale}) rotate(${isMinimal ? 0 : -2}deg)`,
                        filter: isCyberpunk ? 'contrast(1.2) brightness(1.1)' : 'none'
                    }}>
                        <MacWindow title={feature.title} style={{
                            width: 800,
                            height: 500,
                            boxShadow: isCyberpunk ? `0 0 40px ${accentColor}88` : '0 30px 60px rgba(0,0,0,0.3)',
                            borderRadius: isMinimal ? 4 : 12
                        }}>
                            <Img src={staticFile(feature.imagePath)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                        </MacWindow>
                    </div>
                ) : (
                    <div style={{
                        width: 600,
                        height: 400,
                        backgroundColor: 'rgba(255,255,255,0.1)',
                        borderRadius: 20,
                        border: `2px dashed ${textColor}`,
                        display: 'flex',
                        justifyContent: 'center',
                        alignItems: 'center'
                    }}>
                        <span style={{ color: textColor, opacity: 0.5 }}>Image Placeholder: {feature.imagePath}</span>
                    </div>
                )}
            </div>
        </AbsoluteFill>
    );
};
