import React from 'react';
import {AbsoluteFill, interpolate, random, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {c, Level, levelColor, mono, sans, serif} from './theme';

export const Ground: React.FC<{children: React.ReactNode}> = ({children}) => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(1200px 700px at 70% 20%, ${c.accentSoft} 0%, transparent 60%), ${c.bg}`,
      color: c.text,
      fontFamily: sans,
    }}
  >
    {children}
  </AbsoluteFill>
);

export const useRise = (delay = 0, damping = 200) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: frame - delay, fps, config: {damping}});
};

export const Rise: React.FC<{delay?: number; y?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({
  delay = 0,
  y = 30,
  children,
  style,
}) => {
  const p = useRise(delay);
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * y}px)`, ...style}}>{children}</div>;
};

export const Kicker: React.FC<{children: React.ReactNode; color?: string}> = ({children, color = c.accent}) => (
  <div style={{fontFamily: mono, fontSize: 26, letterSpacing: 4, textTransform: 'uppercase', color}}>{children}</div>
);

export const Headline: React.FC<{children: React.ReactNode; size?: number}> = ({children, size = 84}) => (
  <div style={{fontFamily: serif, fontSize: size, fontWeight: 500, lineHeight: 1.08, letterSpacing: -1}}>{children}</div>
);

export const LevelTag: React.FC<{level: Level; size?: number}> = ({level, size = 22}) => {
  const col = levelColor[level];
  const solid = level === 'panic';
  return (
    <span
      style={{
        fontFamily: mono,
        fontSize: size,
        fontWeight: 600,
        padding: '3px 12px',
        borderRadius: 6,
        color: solid ? c.text : col,
        background: solid ? col : 'transparent',
        border: `1.5px solid ${col}`,
        textTransform: 'uppercase',
      }}
    >
      {level}
    </span>
  );
};

const SERVICES = ['payments', 'checkout', 'auth', 'api-gw', 'worker', 'search', 'orders', 'billing'];
const NOISE = [
  'GET /healthz 200 1ms',
  'cache hit key=session:8f2c',
  'scheduled job sync-inventory finished in 412ms',
  'request completed status=200 latency=38ms',
  'retrying upstream call attempt=1',
  'connection pool stats idle=18 active=4',
  'deprecated config key ignored',
  'slow query 820ms SELECT * FROM orders',
  'GC pause 14ms heap=512MB',
  'POST /v1/cart 201 22ms',
];
const LOUD: Record<'warn' | 'error' | 'fatal' | 'panic', string[]> = {
  warn: ['disk usage 86% on /var/lib/postgresql', 'rate limit nearing for tenant acme'],
  error: ['upstream timeout after 30s calling ledger-svc', 'failed to publish event: broker unavailable'],
  fatal: ['pq: remaining connection slots are reserved for non-replication superuser connections'],
  panic: ['runtime error: invalid memory address or nil pointer dereference'],
};

export type LogLine = {level: Level; service: string; msg: string; time: string};

export const logLine = (i: number): LogLine => {
  const r = random(`lvl-${i}`);
  const level: Level =
    i % 23 === 7 ? 'fatal' : i % 31 === 13 ? 'panic' : r < 0.06 ? 'error' : r < 0.16 ? 'warn' : r < 0.6 ? 'info' : 'debug';
  const pool = level === 'info' || level === 'debug' ? NOISE : LOUD[level];
  const msg = pool[Math.floor(random(`msg-${i}`) * pool.length)];
  const service = SERVICES[Math.floor(random(`svc-${i}`) * SERVICES.length)];
  const s = 5 + (i % 55);
  const time = `02:13:${String(s).padStart(2, '0')}.${String(Math.floor(random(`ms-${i}`) * 999)).padStart(3, '0')}`;
  return {level, service, msg, time};
};

export const LogRow: React.FC<{line: LogLine; dim?: number; size?: number}> = ({line, dim = 1, size = 22}) => (
  <div
    style={{
      fontFamily: mono,
      fontSize: size,
      whiteSpace: 'nowrap',
      display: 'flex',
      gap: 18,
      opacity: dim,
      lineHeight: 1.65,
    }}
  >
    <span style={{color: c.subtle}}>{line.time}</span>
    <span style={{color: levelColor[line.level], width: 80, textTransform: 'uppercase', fontWeight: 600}}>
      {line.level}
    </span>
    <span style={{color: c.muted, width: 130}}>{line.service}</span>
    <span style={{color: c.text}}>{line.msg}</span>
  </div>
);

/** The alert message exactly as every channel renders it (src/herjev/formatting.py, README example). */
export const ALERT = [
  ['loại', 'fatal'],
  ['thời gian', '02:13:05 30/10'],
  ['nội dung', 'FATAL pq: remaining connection slots are reserved for non-replication superuser connections'],
  ['service', 'payments'],
  ['root cause dự đoán', 'Connection leak in payments after the 01:50 deploy (pool never returns conns)'],
  ['open', 'https://herjev.example.workers.dev/tickets/9480943d491588a4?occ=3f9c1a2b7d10'],
] as const;

export const AlertText: React.FC<{size?: number; reveal?: number}> = ({size = 22, reveal = ALERT.length}) => (
  <div style={{fontFamily: mono, fontSize: size, lineHeight: 1.55}}>
    {ALERT.map(([k, v], i) => (
      <div key={k} style={{opacity: interpolate(reveal - i, [0, 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}}>
        <span style={{color: c.muted}}>{k}: </span>
        <span style={{color: k === 'loại' ? c.fatal : k === 'open' ? c.accent : c.text}}>{v}</span>
      </div>
    ))}
  </div>
);

export const Card: React.FC<{children: React.ReactNode; style?: React.CSSProperties; edge?: string}> = ({
  children,
  style,
  edge,
}) => (
  <div
    style={{
      background: c.surface,
      border: `1.5px solid ${c.border}`,
      borderLeft: edge ? `6px solid ${edge}` : `1.5px solid ${c.border}`,
      borderRadius: 18,
      padding: 32,
      boxShadow: '0 30px 80px rgba(0,0,0,0.45)',
      ...style,
    }}
  >
    {children}
  </div>
);

export const Wordmark: React.FC<{size?: number}> = ({size = 160}) => (
  <div style={{display: 'flex', alignItems: 'center', gap: size * 0.18}}>
    <svg width={size * 0.72} height={size * 0.72} viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="44" fill="none" stroke={c.accent} strokeWidth="6" />
      <path d="M14 56 L32 56 L40 34 L52 74 L60 46 L66 56 L86 56" fill="none" stroke={c.text} strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="78" cy="24" r="9" fill={c.fatal} />
    </svg>
    <div style={{fontFamily: serif, fontSize: size, fontWeight: 600, letterSpacing: -3, lineHeight: 1}}>herjev</div>
  </div>
);
