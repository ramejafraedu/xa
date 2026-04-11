import React from 'react';

export const FontLoader: React.FC<{ fontFamily: string }> = ({ fontFamily }) => {
    // Extract font name from string like "Inter, sans-serif"
    const fontName = fontFamily.split(',')[0].replace(/['"]/g, '').trim();

    // Safety check
    const systemFonts = ['sans-serif', 'serif', 'monospace', 'system-ui', 'Arial', 'Helvetica', 'Times New Roman'];
    if (systemFonts.includes(fontName)) return null;

    // Use Google Fonts API
    const encodedFamily = encodeURIComponent(fontName).replace(/%20/g, '+');
    const url = `https://fonts.googleapis.com/css2?family=${encodedFamily}:wght@400;700;900&display=swap`;

    return <style>{`@import url("${url}");`}</style>;
};
