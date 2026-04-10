import React from 'react';
import { AbsoluteFill } from 'remotion';

export const MacWindow: React.FC<{
    children: React.ReactNode;
    title?: string;
    style?: React.CSSProperties;
}> = ({ children, title, style }) => {
    return (
        <div style={{
            borderRadius: 12,
            backgroundColor: 'rgba(20, 20, 20, 0.8)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            boxShadow: '0 20px 50px rgba(0,0,0,0.5)',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            ...style
        }}>
            {/* Title Bar */}
            <div style={{
                height: 40,
                background: 'linear-gradient(to bottom, rgba(255,255,255,0.05), rgba(255,255,255,0.02))',
                borderBottom: '1px solid rgba(255,255,255,0.05)',
                display: 'flex',
                alignItems: 'center',
                padding: '0 16px',
                gap: 8
            }}>
                <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#ff5f56' }} />
                <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#ffbd2e' }} />
                <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#27c93f' }} />
                {title && <span style={{ marginLeft: 12, fontFamily: 'system-ui', fontSize: 12, color: '#aaa' }}>{title}</span>}
            </div>

            {/* Content Content */}
            <div style={{ flex: 1, position: 'relative' }}>
                {children}
            </div>
        </div>
    );
};
