/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/**/*.{js,jsx,html}'],
  theme: {
    extend: {
      colors: {
        neon: '#b11515',
        'neon-dim': '#8a1010',
        warn: '#FF8C00',
        'warn-dim': '#cc7000',
        danger: '#FF3333',
        surface: '#0d0d0d',
        'surface-alt': '#0a0a0a',
        'surface-light': '#1a0a0a',
        panel: '#000000',
        'panel-border': '#1c1c1c',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'Consolas', 'monospace'],
        display: ['"Orbitron"', 'sans-serif'],
      },
      boxShadow: {
        neon: '0 0 5px #b11515, 0 0 20px rgba(177,21,21,0.3)',
        'neon-lg': '0 0 10px #b11515, 0 0 40px rgba(177,21,21,0.4)',
        warn: '0 0 5px #FF8C00, 0 0 20px rgba(255,140,0,0.3)',
      },
      animation: {
        'pulse-neon': 'pulseNeon 2s ease-in-out infinite',
        'slide-up': 'slideUp 0.3s ease-out',
        'fade-in': 'fadeIn 0.3s ease-out',
      },
      keyframes: {
        pulseNeon: {
          '0%, 100%': { boxShadow: '0 0 5px #b11515, 0 0 20px rgba(177,21,21,0.3)' },
          '50%': { boxShadow: '0 0 10px #b11515, 0 0 40px rgba(177,21,21,0.5)' },
        },
        slideUp: {
          '0%': { transform: 'translateY(10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};
