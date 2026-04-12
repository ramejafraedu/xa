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
    audio,
    theme,
    playbook,
    layoutVariant,
    kineticLevel,
    transitionPreset,
    featureCardMode,
}) => {
    const { width, height } = useVideoConfig();
    const isPortrait = height >= width;
    const resolvedTheme = String(style.theme || theme || playbook || 'minimal').toLowerCase();
    const resolvedLayout = style.layoutVariant || layoutVariant || (isPortrait ? 'stacked' : 'split');
    const resolvedKinetic = style.kineticLevel || kineticLevel || 'dynamic';
    const resolvedTransition = style.transitionPreset || transitionPreset || 'slide';
    const resolvedFeatureCard = style.featureCardMode || featureCardMode || 'window';

    const transitionDuration = resolvedTransition === 'swipe' ? 18 : resolvedTransition === 'pulse' ? 24 : 30;

    const pickTransitionDirection = (idx: number): "from-right" | "from-left" | "from-top" | "from-bottom" => {
        if (resolvedTransition === 'swipe') {
            return idx % 2 === 0 ? 'from-right' : 'from-left';
        }
        if (resolvedTransition === 'pulse') {
            return idx % 2 === 0 ? 'from-bottom' : 'from-top';
        }
        return idx % 2 === 0 ? 'from-right' : 'from-bottom';
    };

    // Theme Styles
    const getBgColor = () => {
        switch (resolvedTheme) {
            case 'clean-professional': return '#ffffff';
            case 'flat-motion-graphics': return '#0f172a';
            case 'minimalist-diagram': return '#fafafa';
            case 'anime-ghibli': return '#0a0a1a';
            case 'cyberpunk': return '#0f172a';
            case 'minimal': return '#f8f9fa';
            case 'playful': return '#fff1f2';
            default: return style.primaryColor;
        }
    };

    const getTextColor = () => {
        switch (resolvedTheme) {
            case 'clean-professional': return '#1f2937';
            case 'flat-motion-graphics': return '#f8fafc';
            case 'minimalist-diagram': return '#1a1a2e';
            case 'anime-ghibli': return '#f0e6d3';
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
    const hookFontSize = isPortrait ? 64 : 80;
    const projectNameSize = isPortrait ? 32 : 40;
    const solutionFontSize = isPortrait ? 56 : 70;
    const ctaFontSize = isPortrait ? 76 : 100;
    const buttonFontSize = isPortrait ? 24 : 30;

    // Scene Timings
    const HOOK_DURATION = resolvedKinetic === 'intense' ? 130 : 150;
    const SOLUTION_DURATION = resolvedKinetic === 'intense' ? 130 : 150;
    const FEATURE_DURATION = resolvedKinetic === 'soft' ? 210 : 180;
    const CTA_DURATION = resolvedKinetic === 'intense' ? 135 : 150;

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
                            style={{
                                fontSize: hookFontSize,
                                fontWeight: 'bold',
                                maxWidth: isPortrait ? '88%' : '80%',
                                textAlign: 'center',
                            }}
                            color={textColor}
                        />
                        <div style={{ marginTop: isPortrait ? 28 : 20 }}>
                            <KineticText
                                text={projectInfo.name}
                                delay={10}
                                style={{ fontSize: projectNameSize, opacity: 0.8 }}
                                color={accentColor}
                            />
                        </div>
                    </AbsoluteFill>
                </TransitionSeries.Sequence>

                <TransitionSeries.Transition
                    presentation={slide({ direction: pickTransitionDirection(0) })}
                    timing={linearTiming({ durationInFrames: transitionDuration })}
                />

                {/* Scene 2: Solution */}
                <TransitionSeries.Sequence durationInFrames={SOLUTION_DURATION}>
                    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
                        <KineticText
                            text={script.solution}
                            style={{
                                fontSize: solutionFontSize,
                                fontWeight: 'bold',
                                maxWidth: isPortrait ? '90%' : '80%',
                                textAlign: 'center',
                            }}
                            color={accentColor}
                        />
                    </AbsoluteFill>
                </TransitionSeries.Sequence>

                <TransitionSeries.Transition
                    presentation={slide({ direction: pickTransitionDirection(1) })}
                    timing={linearTiming({ durationInFrames: transitionDuration })}
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
                                theme={resolvedTheme}
                                layoutVariant={resolvedLayout}
                                kineticLevel={resolvedKinetic}
                                featureCardMode={resolvedFeatureCard}
                            />
                        </TransitionSeries.Sequence>
                        {index < script.features.length - 1 && (
                            <TransitionSeries.Transition
                                presentation={slide({ direction: pickTransitionDirection(index + 2) })}
                                timing={linearTiming({ durationInFrames: transitionDuration })}
                            />
                        )}
                    </React.Fragment>
                ))}

                <TransitionSeries.Transition
                    presentation={slide({ direction: pickTransitionDirection(8) })}
                    timing={linearTiming({ durationInFrames: transitionDuration })}
                />

                {/* Scene 4: CTA */}
                <TransitionSeries.Sequence durationInFrames={CTA_DURATION}>
                    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
                        <KineticText
                            text={script.cta}
                            style={{
                                fontSize: ctaFontSize,
                                fontWeight: '900',
                                maxWidth: isPortrait ? '88%' : '80%',
                                textAlign: 'center',
                            }}
                            color={accentColor}
                        />
                        <div style={{
                            marginTop: isPortrait ? 28 : 40,
                            padding: isPortrait ? '16px 42px' : '20px 60px',
                            border: `4px solid ${textColor}`,
                            borderRadius: 50,
                            color: textColor,
                            fontSize: buttonFontSize,
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
    theme: string;
    layoutVariant: "split" | "stacked" | "spotlight";
    kineticLevel: "soft" | "dynamic" | "intense";
    featureCardMode: "window" | "plain";
}> = ({ feature, textColor, accentColor, fontFamily, theme, layoutVariant, kineticLevel, featureCardMode }) => {
    const frame = useCurrentFrame();
    const { fps, width, height } = useVideoConfig();
    const isPortrait = height >= width;

    const springConfig = kineticLevel === 'intense'
        ? { damping: 10, stiffness: 150, mass: 0.9 }
        : kineticLevel === 'soft'
            ? { damping: 18, stiffness: 90, mass: 1.0 }
            : { damping: 12, stiffness: 110, mass: 1.0 };
    const scale = spring({ frame, fps, config: springConfig });
    const yOffset = kineticLevel === 'intense' ? 70 : kineticLevel === 'soft' ? 30 : 50;
    const y = interpolate(frame, [0, 30], [yOffset, 0], { extrapolateRight: 'clamp' });
    const mediaWidth = isPortrait ? Math.min(Math.floor(width * 0.86), 860) : 800;
    const mediaHeight = isPortrait ? Math.min(Math.floor(height * 0.34), 620) : 500;
    const placeholderWidth = isPortrait ? Math.min(Math.floor(width * 0.76), 720) : 600;
    const placeholderHeight = isPortrait ? Math.min(Math.floor(height * 0.28), 520) : 400;

    // --- Theme Specific Styles ---
    const isCyberpunk = theme === 'cyberpunk';
    const isMinimal = theme === 'minimal';
    const effectiveLayout = layoutVariant === 'spotlight'
        ? 'spotlight'
        : layoutVariant === 'stacked'
            ? 'stacked'
            : (isPortrait ? 'stacked' : 'split');

    // Cyberpunk specific glitch/skew
    const skew = isCyberpunk ? Math.sin(frame / 10) * 5 : 0;
    const opacity = isCyberpunk ? interpolate(frame % 20, [0, 10, 20], [1, 0.8, 1]) : 1;

    // Minimal specific fade
    const minimalOpacity = isMinimal ? interpolate(frame, [0, 20], [0, 1]) : 1;

    return (
        <AbsoluteFill style={{
            justifyContent: 'center',
            alignItems: 'center',
            flexDirection: effectiveLayout === 'split' ? 'row' : 'column',
            gap: isPortrait ? 28 : 60,
            fontFamily,
            opacity: isMinimal ? minimalOpacity : 1
        }}>
            {/* Text Side */}
            <div style={{
                flex: effectiveLayout === 'split' ? 1 : '0 0 auto',
                width: effectiveLayout === 'split' ? 'auto' : '88%',
                paddingLeft: effectiveLayout === 'split' ? 100 : 0,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                alignItems: effectiveLayout === 'split' ? 'flex-start' : 'center',
                textAlign: effectiveLayout === 'split' ? 'left' : 'center',
            }}>
                <h2 style={{
                    color: accentColor,
                    fontSize: isPortrait ? 52 : 60,
                    margin: 0,
                    transform: `translateY(${y}px) skewX(${skew}deg)`,
                    textShadow: isCyberpunk ? `2px 2px 0px ${textColor}` : 'none',
                    opacity
                }}>
                    {feature.title}
                </h2>
                <h3 style={{
                    color: textColor,
                    fontSize: isPortrait ? 34 : 40,
                    margin: isPortrait ? '14px 0 0 0' : '20px 0 0 0',
                    opacity: 0.8,
                    fontWeight: isMinimal ? 300 : 700
                }}>
                    {feature.subtitle}
                </h3>
            </div>

            {/* Image Side */}
            <div style={{
                flex: effectiveLayout === 'split' ? 1.5 : '0 0 auto',
                width: effectiveLayout === 'split' ? 'auto' : '100%',
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                paddingRight: effectiveLayout === 'split' ? 100 : 0,
            }}>
                {feature.imagePath ? (
                    <div style={{
                        transform: `scale(${scale}) rotate(${isMinimal ? 0 : -2}deg)`,
                        filter: isCyberpunk ? 'contrast(1.2) brightness(1.1)' : 'none'
                    }}>
                        {featureCardMode === 'plain' ? (
                            <div style={{
                                width: mediaWidth,
                                height: mediaHeight,
                                overflow: 'hidden',
                                borderRadius: isMinimal ? 8 : 16,
                                border: `2px solid ${accentColor}`,
                                boxShadow: isCyberpunk ? `0 0 40px ${accentColor}88` : '0 30px 60px rgba(0,0,0,0.25)',
                            }}>
                                <Img src={staticFile(feature.imagePath)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                            </div>
                        ) : (
                            <MacWindow title={feature.title} style={{
                                width: mediaWidth,
                                height: mediaHeight,
                                boxShadow: isCyberpunk ? `0 0 40px ${accentColor}88` : '0 30px 60px rgba(0,0,0,0.3)',
                                borderRadius: isMinimal ? 4 : 12
                            }}>
                                <Img src={staticFile(feature.imagePath)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                            </MacWindow>
                        )}
                    </div>
                ) : (
                    <div style={{
                        width: placeholderWidth,
                        height: placeholderHeight,
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
