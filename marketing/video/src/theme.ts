import {loadFont as loadSans} from '@remotion/google-fonts/BeVietnamPro';
import {loadFont as loadSerif} from '@remotion/google-fonts/Newsreader';
import {loadFont as loadMono} from '@remotion/google-fonts/JetBrainsMono';

const subsets: ('latin' | 'vietnamese')[] = ['latin', 'vietnamese'];
export const sans = loadSans('normal', {weights: ['400', '500', '600', '700'], subsets}).fontFamily;
export const serif = loadSerif('normal', {weights: ['500', '600'], subsets}).fontFamily;
export const mono = loadMono('normal', {weights: ['400', '600'], subsets}).fontFamily;

// Colours follow the dashboard tokens (dashboard/src/styles/tokens.css), on a dark ink ground.
export const c = {
  bg: 'oklch(17% 0.01 70)',
  bg2: 'oklch(21% 0.01 70)',
  surface: 'oklch(25% 0.012 70)',
  border: 'oklch(34% 0.012 70)',
  text: 'oklch(97.4% 0.006 70)',
  muted: 'oklch(70% 0.014 70)',
  subtle: 'oklch(52% 0.014 70)',
  accent: 'oklch(68% 0.14 258)',
  accentSoft: 'oklch(30% 0.06 258)',
  success: 'oklch(70% 0.14 150)',
  panic: 'oklch(58% 0.21 27)',
  fatal: 'oklch(66% 0.2 27)',
  error: 'oklch(74% 0.14 58)',
  warn: 'oklch(84% 0.12 92)',
  info: 'oklch(72% 0.03 250)',
};

export type Level = 'info' | 'debug' | 'warn' | 'error' | 'fatal' | 'panic';
export const levelColor: Record<Level, string> = {
  info: c.info,
  debug: c.subtle,
  warn: c.warn,
  error: c.error,
  fatal: c.fatal,
  panic: c.panic,
};
