import { useCurrentFrame, useVideoConfig, interpolate, spring } from 'remotion';
import React from 'react';

export const KineticText: React.FC<{
    text: string;
    style?: React.CSSProperties;
    delay?: number;
    color?: string;
}> = ({ text, style, delay = 0, color = 'white' }) => {
    const frame = useCurrentFrame();
    const { fps } = useVideoConfig();

    // Split by newline first to handle manual breaks
    const lines = text.split('\n');
    let globalIndex = 0;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', ...style }}>
            {lines.map((line, lineIndex) => {
                // Determine segments for this line
                const segments = line.includes(' ') ? line.split(' ') : line.split('');

                return (
                    <div key={lineIndex} style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '0.3em' }}>
                        {segments.map((word, i) => {
                            const wordStart = delay + globalIndex * 5;
                            globalIndex++; // Increment global index for continuous staggering

                            const progress = spring({
                                frame: frame - wordStart,
                                fps,
                                config: { damping: 12, stiffness: 200 }
                            });

                            const y = interpolate(progress, [0, 1], [50, 0]);
                            const opacity = interpolate(progress, [0, 1], [0, 1]);
                            const blur = interpolate(progress, [0, 1], [10, 0]);

                            return (
                                <span key={i} style={{
                                    display: 'inline-block',
                                    opacity,
                                    transform: `translateY(${y}px)`,
                                    filter: `blur(${blur}px)`,
                                    color,
                                    fontFamily: 'Inter, system-ui, sans-serif',
                                    fontWeight: 800,
                                }}>
                                    {word}
                                </span>
                            );
                        })}
                    </div>
                );
            })}
        </div>
    );
};
