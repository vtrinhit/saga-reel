import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/** tailwind-merge biết các token riêng của DESIGN.md để không gộp nhầm (vd `outline-focus` với `outline-accent`). */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['2xs', 'xs', 'sm', 'base', 'md', 'lg', 'xl', '2xl', '3xl'] }],
      'outline-w': [{ outline: ['focus'] }],
      'outline-offset': [{ 'outline-offset': ['focus'] }],
      shadow: [{ shadow: ['pop', 'modal'] }],
      h: [{ h: ['control', 'touch', 'row', 'track', 'topbar', 'meta'] }],
      w: [{ w: ['rail', 'inspector'] }],
      'min-h': [{ 'min-h': ['touch'] }],
      z: [{ z: ['sticky', 'playhead', 'dropdown', 'drawer', 'batchbar', 'modal', 'toast', 'palette', 'banner', 'tooltip'] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Lớp focus chung (DESIGN §3.7): outline accent nằm ngoài phần tử. */
export const focusRing =
  'focus-visible:outline focus-visible:outline-focus focus-visible:outline-offset-focus focus-visible:outline-accent';
