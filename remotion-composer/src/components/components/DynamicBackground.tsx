import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';

type Theme = "cyberpunk" | "minimal" | "playful";

export const DynamicBackground: React.FC<{
    theme: Theme;
    primaryColor: string;
    accentColor: string;
}> = ({ theme, primaryColor, accentColor }) => {
    const frame = useCurrentFrame();
    const { width, height } = useVideoConfig();

    if (theme === 'cyberpunk') {
        const gridSize = 50;
        const offset = (frame * 2) % gridSize;
        return (
            <AbsoluteFill style={{ overflow: 'hidden' }}>
                {/* Moving Grid */}
                <div style={{
                    position: 'absolute',
                    top: -gridSize,
                    left: 0,
                    width: '100%',
                    height: height + gridSize,
                    background: `
                        linear-gradient(to right, ${accentColor}11 1px, transparent 1px),
                        linear-gradient(to bottom, ${accentColor}11 1px, transparent 1px)
                    `,
                    backgroundSize: `${gridSize}px ${gridSize}px`,
                    transform: `translateY(${offset}px) perspective(500px) rotateX(10deg)`,
                    transformOrigin: 'top'
                }} />
                {/* Glow Orb */}
                <div style={{
                    position: 'absolute',
                    top: '20%',
                    left: '50%',
                    width: 600,
                    height: 600,
                    background: `radial-gradient(circle, ${accentColor}33 0%, transparent 70%)`,
                    transform: `translate(-50%, -50%) scale(${1 + Math.sin(frame / 30) * 0.1})`,
                    filter: 'blur(50px)'
                }} />
            </AbsoluteFill>
        );
    }

    if (theme === 'minimal') {
        return (
            <AbsoluteFill style={{ overflow: 'hidden' }}>
                <div style={{
                    position: 'absolute',
                    width: '200%',
                    height: '200%',
                    top: '-50%',
                    left: '-50%',
                    background: `radial-gradient(circle at center, ${primaryColor}00, ${primaryColor}11)`,
                    transform: `rotate(${frame / 10}deg)`,
                }} />
            </AbsoluteFill>
        );
    }

    // Playful
    return (
        <AbsoluteFill style={{ overflow: 'hidden' }}>
            {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} style={{
                    position: 'absolute',
                    top: `${Math.random() * 100}%`,
                    left: `${(i * 20) + Math.sin((frame + i * 100) / 50) * 10}%`,
                    width: 100,
                    height: 100,
                    borderRadius: '50%',
                    background: accentColor,
                    opacity: 0.1,
                    transform: `translateY(${-frame * (1 + i * 0.5)}px)`
                }} />
            ))}
        </AbsoluteFill>
    );
};
