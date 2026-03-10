const plugin = require('tailwindcss/plugin');

/**
 * Tool Design System — Tailwind CSS Preset
 *
 * Usage in your tailwind.config.js:
 *   module.exports = {
 *     presets: [require('tool-design-system/preset')],
 *     content: [...],
 *   }
 *
 * Override the accent color per-tool with CSS custom properties:
 *   :root { --accent-400: #2563eb; --accent-500: #1d4ed8; }
 */
module.exports = {
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        accent: {
          50:  'var(--accent-50, #fffbeb)',
          100: 'var(--accent-100, #fef3c7)',
          200: 'var(--accent-200, #fde68a)',
          300: 'var(--accent-300, #fcd34d)',
          400: 'var(--accent-400, #fbbf24)',
          500: 'var(--accent-500, #f59e0b)',
          600: 'var(--accent-600, #d97706)',
          700: 'var(--accent-700, #b45309)',
          800: 'var(--accent-800, #92400e)',
          900: 'var(--accent-900, #78350f)',
          950: 'var(--accent-950, #451a03)',
        },
        surface: {
          DEFAULT: '#1e293b',
          raised:  '#334155',
          overlay: '#0f172a',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'glow-sm':  '0 0 8px -2px var(--accent-400, #fbbf24)',
        'glow':     '0 0 16px -2px var(--accent-400, #fbbf24)',
        'glow-lg':  '0 0 32px -4px var(--accent-400, #fbbf24)',
        'glow-accent': '0 4px 16px -2px color-mix(in srgb, var(--accent-400, #fbbf24) 35%, transparent)',
        'elevation-1': '0 1px 3px 0 rgb(0 0 0 / 0.4), 0 1px 2px -1px rgb(0 0 0 / 0.4)',
        'elevation-2': '0 4px 6px -1px rgb(0 0 0 / 0.4), 0 2px 4px -2px rgb(0 0 0 / 0.4)',
        'elevation-3': '0 10px 15px -3px rgb(0 0 0 / 0.5), 0 4px 6px -4px rgb(0 0 0 / 0.5)',
        'elevation-4': '0 20px 25px -5px rgb(0 0 0 / 0.5), 0 8px 10px -6px rgb(0 0 0 / 0.5)',
      },
      borderRadius: {
        'pill': '9999px',
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }],
      },
      animation: {
        'shimmer':        'shimmer 1.4s ease infinite',
        'slide-in-right': 'slideInRight 0.2s ease-out',
        'slide-in-up':    'slideInUp 0.2s ease-out',
        'slide-in-down':  'slideInDown 0.2s ease-out',
        'fade-in':        'fadeIn 0.15s ease-out',
        'fade-out':       'fadeOut 0.15s ease-in',
        'pulse-dot':      'pulseDot 1.5s ease-in-out infinite',
        'scale-in':       'scaleIn 0.15s ease-out',
        'spin-slow':      'spin 2s linear infinite',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        slideInRight: {
          '0%':   { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        slideInUp: {
          '0%':   { transform: 'translateY(8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        slideInDown: {
          '0%':   { transform: 'translateY(-8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        fadeOut: {
          '0%':   { opacity: '1' },
          '100%': { opacity: '0' },
        },
        pulseDot: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%':      { opacity: '0.5', transform: 'scale(0.85)' },
        },
        scaleIn: {
          '0%':   { transform: 'scale(0.95)', opacity: '0' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
      },
      transitionDuration: {
        '0': '0ms',
      },
      brightness: {
        '110': '1.1',
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-accent':  'linear-gradient(135deg, var(--accent-400, #fbbf24), var(--accent-500, #f59e0b))',
        'dot-pattern':      'radial-gradient(circle, #334155 1px, transparent 1px)',
      },
      backgroundSize: {
        'dots': '24px 24px',
      },
    },
  },
  plugins: [
    plugin(function({ addBase, addUtilities }) {
      addBase({
        /* Smooth scrolling */
        'html': {
          scrollBehavior: 'smooth',
          '-webkit-font-smoothing': 'antialiased',
          '-moz-osx-font-smoothing': 'grayscale',
        },
        /* Dark scrollbar */
        '*': {
          scrollbarWidth: 'thin',
          scrollbarColor: '#475569 transparent',
        },
        '::-webkit-scrollbar': {
          width: '6px',
          height: '6px',
        },
        '::-webkit-scrollbar-track': {
          background: 'transparent',
        },
        '::-webkit-scrollbar-thumb': {
          background: '#475569',
          borderRadius: '3px',
        },
        '::-webkit-scrollbar-thumb:hover': {
          background: '#64748b',
        },
      });
      addUtilities({
        '.glass': {
          background: 'rgba(30, 41, 59, 0.6)',
          backdropFilter: 'blur(12px)',
          '-webkit-backdrop-filter': 'blur(12px)',
        },
        '.glass-heavy': {
          background: 'rgba(30, 41, 59, 0.8)',
          backdropFilter: 'blur(20px)',
          '-webkit-backdrop-filter': 'blur(20px)',
        },
        '.text-gradient': {
          backgroundImage: 'linear-gradient(135deg, var(--accent-300, #fcd34d), var(--accent-500, #f59e0b))',
          '-webkit-background-clip': 'text',
          '-webkit-text-fill-color': 'transparent',
          backgroundClip: 'text',
        },
        '.ring-glow': {
          boxShadow: '0 0 0 1px var(--accent-400, #fbbf24), 0 0 12px -2px var(--accent-400, #fbbf24)',
        },
      });
    }),
  ],
};
