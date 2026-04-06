/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/**/*.{js,jsx,html}'],
  theme: {
    extend: {
      colors: {
        neon: '#00FF00',
        'neon-dim': '#00cc00',
        warn: '#FF8C00',
        'warn-dim': '#cc7000',
        danger: '#FF3333',
        surface: '#1a1a2e',
        'surface-alt': '#16213e',
        'surface-light': '#0f3460',
        panel: '#0d1117',
        'panel-border': '#21262d',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'Consolas', 'monospace'],
        display: ['"Orbitron"', 'sans-serif'],
      },
      boxShadow: {
        neon: '0 0 5px #00FF00, 0 0 20px rgba(0,255,0,0.3)',
        'neon-lg': '0 0 10px #00FF00, 0 0 40px rgba(0,255,0,0.4)',
        warn: '0 0 5px #FF8C00, 0 0 20px rgba(255,140,0,0.3)',
      },
      animation: {
        'pulse-neon': 'pulseNeon 2s ease-in-out infinite',
        'slide-up': 'slideUp 0.3s ease-out',
        'fade-in': 'fadeIn 0.3s ease-out',
      },
      keyframes: {
        pulseNeon: {
          '0%, 100%': { boxShadow: '0 0 5px #00FF00, 0 0 20px rgba(0,255,0,0.3)' },
          '50%': { boxShadow: '0 0 10px #00FF00, 0 0 40px rgba(0,255,0,0.5)' },
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
