import type { Config } from 'tailwindcss';
const c = (v: string) => `rgb(var(--c-${v}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    screens: { sm: '480px', md: '768px', lg: '1024px', xl: '1280px', '2xl': '1600px', '3xl': '1920px' },
    extend: {
      colors: {
        stage: c('stage'),
        bg: { 0: c('bg-0'), 1: c('bg-1'), 2: c('bg-2'), 3: c('bg-3'), 4: c('bg-4') },
        line: { 1: c('line-1'), 2: c('line-2'), 3: c('line-3') },
        fg: { 1: c('fg-1'), 2: c('fg-2'), 3: c('fg-3'), 4: c('fg-4') },
        accent: { DEFAULT: c('accent'), hi: c('accent-hi'), lo: c('accent-lo'), on: c('on-accent') },
        state: {
          queued: c('queued'), running: c('running'), done: c('done'), failed: c('failed'),
          stale: c('stale'), drifted: c('drifted'), approved: c('approved'),
        },
        warn: c('warn'),
        ok: c('ok'),
        track: {
          video: c('track-video'), dialogue: c('track-dialogue'), subtitle: c('track-subtitle'),
          music: c('track-music'), sfx: c('track-sfx'),
        },
      },
      fontFamily: { sans: ['var(--font-ui)'], mono: ['var(--font-mono)'] },
      fontSize: Object.fromEntries(
        ['2xs', 'xs', 'sm', 'base', 'md', 'lg', 'xl', '2xl', '3xl'].map((k) => [
          k, [`var(--text-${k})`, { lineHeight: `var(--lh-${k})` }],
        ]),
      ),
      borderRadius: { xs: 'var(--r-xs)', sm: 'var(--r-sm)', md: 'var(--r-md)', lg: 'var(--r-lg)' },
      boxShadow: { pop: 'var(--shadow-pop)', modal: 'var(--shadow-modal)' },
      outlineWidth: { focus: 'var(--focus-w)' },
      outlineOffset: { focus: 'var(--focus-offset)' },
      height: { control: 'var(--h-control)', touch: 'var(--h-touch)', row: 'var(--h-row)', track: 'var(--h-track)', topbar: 'var(--h-topbar)', meta: 'var(--h-meta)', spine: 'var(--h-spine)', bottomnav: 'var(--h-bottomnav)' },
      minHeight: { touch: 'var(--h-touch)' },
      width: { rail: 'var(--w-rail)', inspector: 'var(--w-inspector)', activity: 'var(--w-activity)', queue: 'var(--w-queue)' },
      borderWidth: { ring: 'var(--ring-w)' },
      zIndex: {
        sticky: 'var(--z-sticky)', playhead: 'var(--z-playhead)', dropdown: 'var(--z-dropdown)', drawer: 'var(--z-drawer)',
        batchbar: 'var(--z-batchbar)', modal: 'var(--z-modal)', toast: 'var(--z-toast)', palette: 'var(--z-palette)',
        banner: 'var(--z-banner)', tooltip: 'var(--z-tooltip)',
      },
      transitionDuration: { fast: 'var(--dur-fast)', base: 'var(--dur-base)', slow: 'var(--dur-slow)', exit: 'var(--dur-exit)', image: 'var(--dur-image)' },
      transitionTimingFunction: { out: 'var(--ease-out)', in: 'var(--ease-in)', inout: 'var(--ease-inout)' },
      aspectRatio: { portrait: '9 / 16', landscape: '16 / 9', square: '1 / 1' },
    },
  },
  plugins: [],
} satisfies Config;
