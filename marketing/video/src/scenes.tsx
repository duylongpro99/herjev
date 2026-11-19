import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {AlertText, Card, Ground, Headline, Kicker, LevelTag, logLine, LogRow, Rise, useRise, Wordmark} from './parts';
import {c, Level, levelColor, mono} from './theme';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// 1. The flood: logs that never stop.
export const Flood: React.FC = () => {
  const frame = useCurrentFrame();
  const offset = frame * 9;
  const first = Math.floor(offset / 36);
  const count = Math.round(interpolate(frame, [0, 150], [1200, 12480], clamp));
  return (
    <Ground>
      <AbsoluteFill style={{padding: '0 60px', transform: `translateY(${-(offset % 36)}px)`, opacity: 0.55}}>
        {Array.from({length: 34}, (_, k) => (
          <LogRow key={first + k} line={logLine(first + k)} />
        ))}
      </AbsoluteFill>
      <AbsoluteFill style={{background: `linear-gradient(90deg, ${c.bg} 30%, transparent 85%)`}} />
      <AbsoluteFill style={{justifyContent: 'center', padding: 120, gap: 28}}>
        <Rise delay={8}>
          <Kicker color={c.warn}>02:13 sáng · production</Kicker>
        </Rise>
        <Rise delay={16}>
          <Headline size={96}>
            Server của bạn
            <br />
            không bao giờ im lặng.
          </Headline>
        </Rise>
        <Rise delay={30}>
          <div style={{fontFamily: mono, fontSize: 34, color: c.muted}}>
            <span style={{color: c.text, fontWeight: 600}}>{count.toLocaleString('en-US')}</span> dòng log / phút
          </div>
        </Rise>
      </AbsoluteFill>
    </Ground>
  );
};

// 2. Sources in, Jev triage keeps only what deserves a human.
const SOURCES = ['ssh · tail -F', 'kubectl logs', 'journalctl', 'loki', 'http_poll', 'webhook', 'syslog', 'file'];
const TRIAGE_ROWS = [3, 7, 9, 11, 13, 16, 19, 22, 26, 30].map(logLine);
const loud = (l: Level) => l === 'warn' || l === 'error' || l === 'fatal' || l === 'panic';

export const Triage: React.FC = () => {
  const frame = useCurrentFrame();
  const kept = TRIAGE_ROWS.filter((l) => l.level === 'fatal' || l.level === 'panic' || l.level === 'error').length;
  return (
    <Ground>
      <div style={{position: 'absolute', left: 120, top: 100, right: 120, display: 'flex', flexDirection: 'column', gap: 22}}>
        <Rise>
          <Kicker>1 · thu thập → 2 · Jev triage</Kicker>
        </Rise>
        <Rise delay={6}>
          <Headline size={72}>Jev lọc nhiễu, chỉ giữ sự cố đáng đánh thức người.</Headline>
        </Rise>
      </div>
      <div style={{position: 'absolute', left: 120, top: 380, width: 300, display: 'flex', flexDirection: 'column', gap: 20}}>
        {SOURCES.map((s, i) => (
          <Rise key={s} delay={14 + i * 3} y={14}>
            <div
              style={{
                fontFamily: mono,
                fontSize: 24,
                padding: '12px 20px',
                borderRadius: 10,
                border: `1.5px solid ${c.border}`,
                background: c.bg2,
                color: c.muted,
              }}
            >
              {s}
            </div>
          </Rise>
        ))}
      </div>
      <Card style={{position: 'absolute', left: 480, top: 380, right: 120, padding: '26px 30px'}}>
        {TRIAGE_ROWS.map((line, i) => {
          const t = 50 + i * 7;
          const serious = line.level === 'fatal' || line.level === 'panic' || line.level === 'error';
          const decided = interpolate(frame, [t, t + 12], [0, 1], clamp);
          const dim = serious ? 1 : interpolate(decided, [0, 1], [1, loud(line.level) ? 0.32 : 0.16]);
          return (
            <div key={i} style={{display: 'flex', alignItems: 'center', gap: 16, position: 'relative'}}>
              <div style={{flex: 1, overflow: 'hidden'}}>
                <LogRow line={line} dim={dim} size={23} />
              </div>
              <div
                style={{
                  opacity: decided,
                  fontFamily: mono,
                  fontSize: 18,
                  fontWeight: 600,
                  width: 170,
                  textAlign: 'right',
                  color: serious ? levelColor[line.level] : c.subtle,
                }}
              >
                {serious ? '● cần người xem' : 'bỏ qua'}
              </div>
            </div>
          );
        })}
      </Card>
      <div
        style={{
          position: 'absolute',
          right: 120,
          bottom: 60,
          fontFamily: mono,
          fontSize: 24,
          color: c.muted,
          opacity: interpolate(frame, [130, 145], [0, 1], clamp),
        }}
      >
        {TRIAGE_ROWS.length} sự kiện → <span style={{color: c.text, fontWeight: 600}}>{kept} cảnh báo</span>
      </div>
    </Ground>
  );
};

// 3. LLM analysis: root cause and advice.
const SECTIONS: {title: string; body: string[]; code?: boolean}[] = [
  {title: 'Nguyên nhân dự đoán', body: ['Rò rỉ connection trong payments sau bản deploy 01:50 — pool không trả connection.']},
  {title: 'Ảnh hưởng', body: ['Thanh toán lỗi hàng loạt; các service dùng chung Postgres bị chặn kết nối mới.']},
  {title: 'Hành động', body: ['Rollback bản deploy 01:50 của payments', 'Tăng tạm max_connections, theo dõi pool']},
  {
    title: 'Lệnh chẩn đoán',
    body: ["SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1;"],
    code: true,
  },
];

export const Analysis: React.FC = () => (
  <Ground>
    <div style={{position: 'absolute', left: 120, top: 100, width: 640, display: 'flex', flexDirection: 'column', gap: 24}}>
      <Rise>
        <Kicker>3 · phân tích bằng LLM</Kicker>
      </Rise>
      <Rise delay={6}>
        <Headline size={72}>Không chỉ báo lỗi. Giải thích vì sao và nên làm gì.</Headline>
      </Rise>
      <Rise delay={18}>
        <Card edge={c.fatal} style={{padding: 26}}>
          <div style={{display: 'flex', gap: 14, alignItems: 'center', marginBottom: 14}}>
            <LevelTag level="fatal" />
            <span style={{fontFamily: mono, fontSize: 22, color: c.muted}}>payments · 02:13:05</span>
          </div>
          <div style={{fontFamily: mono, fontSize: 22, lineHeight: 1.5}}>
            pq: remaining connection slots are reserved for non-replication superuser connections
          </div>
        </Card>
      </Rise>
    </div>
    <div style={{position: 'absolute', left: 860, top: 120, right: 120, display: 'flex', flexDirection: 'column', gap: 20}}>
      {SECTIONS.map((s, i) => (
        <Rise key={s.title} delay={40 + i * 18} y={24}>
          <Card style={{padding: '22px 28px'}}>
            <div style={{fontFamily: mono, fontSize: 18, letterSpacing: 2, textTransform: 'uppercase', color: c.accent, marginBottom: 10}}>
              {s.title}
            </div>
            {s.body.map((b) => (
              <div
                key={b}
                style={{
                  fontFamily: s.code ? mono : undefined,
                  fontSize: s.code ? 19 : 26,
                  lineHeight: 1.45,
                  color: s.code ? c.success : c.text,
                }}
              >
                {s.body.length > 1 ? '→ ' : ''}
                {b}
              </div>
            ))}
          </Card>
        </Rise>
      ))}
    </div>
  </Ground>
);

// 4. One message, every channel.
const CHANNELS = ['Telegram', 'Slack', 'Discord', 'Microsoft Teams', 'Webhook (JSON)', 'Console'];

export const Delivery: React.FC = () => {
  const frame = useCurrentFrame();
  const reveal = interpolate(frame, [30, 90], [0, 6], clamp);
  return (
    <Ground>
      <div style={{position: 'absolute', left: 120, top: 100, right: 120, display: 'flex', flexDirection: 'column', gap: 22}}>
        <Rise>
          <Kicker>4 · gửi đi</Kicker>
        </Rise>
        <Rise delay={6}>
          <Headline size={72}>Một định dạng. Mọi kênh.</Headline>
        </Rise>
      </div>
      <Card edge={c.fatal} style={{position: 'absolute', left: 120, top: 360, width: 1080, padding: 36}}>
        <AlertText size={23} reveal={reveal} />
      </Card>
      <div style={{position: 'absolute', left: 1280, top: 360, right: 120, display: 'flex', flexDirection: 'column', gap: 18}}>
        {CHANNELS.map((ch, i) => {
          const on = interpolate(frame, [100 + i * 10, 112 + i * 10], [0, 1], clamp);
          return (
            <div
              key={ch}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '16px 24px',
                borderRadius: 14,
                fontSize: 28,
                fontWeight: 600,
                background: c.bg2,
                border: `1.5px solid ${on > 0.5 ? c.success : c.border}`,
                color: on > 0.5 ? c.text : c.subtle,
              }}
            >
              {ch}
              <span style={{fontFamily: mono, fontSize: 22, color: c.success, opacity: on}}>✓ đã gửi</span>
            </div>
          );
        })}
      </div>
    </Ground>
  );
};

// 5. A chat agent follows up through herjev's MCP tools.
const Bubble: React.FC<{from: 'user' | 'agent'; delay: number; children: React.ReactNode}> = ({from, delay, children}) => {
  const p = useRise(delay);
  const user = from === 'user';
  return (
    <div style={{display: 'flex', justifyContent: user ? 'flex-end' : 'flex-start', opacity: p, transform: `translateY(${(1 - p) * 20}px)`}}>
      <div
        style={{
          maxWidth: 820,
          padding: '20px 28px',
          borderRadius: 22,
          fontSize: 28,
          lineHeight: 1.45,
          background: user ? c.accent : c.surface,
          color: user ? c.bg : c.text,
          border: user ? 'none' : `1.5px solid ${c.border}`,
        }}
      >
        {children}
      </div>
    </div>
  );
};

const Tool: React.FC<{name: string}> = ({name}) => (
  <span
    style={{
      fontFamily: mono,
      fontSize: 20,
      padding: '4px 12px',
      borderRadius: 8,
      background: c.accentSoft,
      color: c.accent,
      marginRight: 10,
    }}
  >
    {name}
  </span>
);

export const Agent: React.FC = () => (
  <Ground>
    <div style={{position: 'absolute', left: 120, top: 100, width: 620, display: 'flex', flexDirection: 'column', gap: 24}}>
      <Rise>
        <Kicker>5 · hỏi tiếp trong chat</Kicker>
      </Rise>
      <Rise delay={6}>
        <Headline size={72}>Agent của bạn hỏi tiếp qua MCP.</Headline>
      </Rise>
      <Rise delay={14}>
        <div style={{fontSize: 28, color: c.muted, lineHeight: 1.5}}>
          Tra sự cố, phân tích log dán vào, tắt cảnh báo ồn — không shell, không truy cập file.
        </div>
      </Rise>
    </div>
    <div style={{position: 'absolute', left: 820, top: 150, right: 120, display: 'flex', flexDirection: 'column', gap: 22}}>
      <Bubble from="user" delay={24}>
        Sự cố payments lúc 2h sáng là gì?
      </Bubble>
      <Bubble from="agent" delay={46}>
        <div style={{marginBottom: 10}}>
          <Tool name="incidents" />
          <Tool name="incident" />
        </div>
        FATAL ở payments: hết connection Postgres. Dự đoán rò rỉ pool sau bản deploy 01:50 — nên rollback.
      </Bubble>
      <Bubble from="user" delay={80}>
        Tạm tắt cảnh báo này 60 phút nhé.
      </Bubble>
      <Bubble from="agent" delay={100}>
        <div style={{marginBottom: 10}}>
          <Tool name="mute" />
        </div>
        Đã tắt 60 phút. Sự cố mới vẫn được báo bình thường.
      </Bubble>
    </div>
  </Ground>
);

// 6. Outro.
export const Outro: React.FC = () => (
  <Ground>
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', gap: 40}}>
      <Rise y={40}>
        <Wordmark size={170} />
      </Rise>
      <Rise delay={14}>
        <Headline size={64}>Chỉ những cảnh báo thật sự quan trọng.</Headline>
      </Rise>
      <Rise delay={26}>
        <div style={{fontSize: 28, color: c.muted}}>Log-watch agent · Jev triage · LLM root cause · mọi kênh chat</div>
      </Rise>
      <Rise delay={40}>
        <div
          style={{
            fontFamily: mono,
            fontSize: 32,
            padding: '16px 34px',
            borderRadius: 14,
            background: c.bg2,
            border: `1.5px solid ${c.border}`,
          }}
        >
          <span style={{color: c.success}}>$</span> herjev run
        </div>
      </Rise>
    </AbsoluteFill>
  </Ground>
);
