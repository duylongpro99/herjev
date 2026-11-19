import React from 'react';
import {AbsoluteFill, Composition, registerRoot, Still} from 'remotion';
import {linearTiming, TransitionSeries} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {AlertText, Card, Ground, Headline, Kicker, logLine, LogRow, Wordmark} from './parts';
import {Agent, Analysis, Delivery, Flood, Outro, Triage} from './scenes';
import {c, mono} from './theme';

const SCENES: [React.FC, number][] = [
  [Flood, 150],
  [Triage, 180],
  [Analysis, 210],
  [Delivery, 210],
  [Agent, 180],
  [Outro, 150],
];
const T = 15;
const DURATION = SCENES.reduce((s, [, d]) => s + d, 0) - T * (SCENES.length - 1);

const Intro: React.FC = () => (
  <TransitionSeries>
    {SCENES.flatMap(([Scene, d], i) => [
      ...(i ? [<TransitionSeries.Transition key={`t${i}`} presentation={fade()} timing={linearTiming({durationInFrames: T})} />] : []),
      <TransitionSeries.Sequence key={`s${i}`} durationInFrames={d}>
        <Scene />
      </TransitionSeries.Sequence>,
    ])}
  </TransitionSeries>
);

const BackdropLogs: React.FC<{rows: number; size?: number}> = ({rows, size = 22}) => (
  <AbsoluteFill style={{padding: '20px 50px', opacity: 0.35}}>
    {Array.from({length: rows}, (_, i) => (
      <LogRow key={i} line={logLine(i + 40)} size={size} />
    ))}
  </AbsoluteFill>
);

const Hero: React.FC = () => (
  <Ground>
    <BackdropLogs rows={30} />
    <AbsoluteFill style={{background: `linear-gradient(90deg, ${c.bg} 45%, color-mix(in oklch, ${c.bg} 80%, transparent) 70%, transparent)`}} />
    <div style={{position: 'absolute', left: 120, top: 190, width: 820, display: 'flex', flexDirection: 'column', gap: 34}}>
      <Wordmark size={130} />
      <Headline size={76}>Chỉ những cảnh báo thật sự quan trọng.</Headline>
      <div style={{fontSize: 30, color: c.muted, lineHeight: 1.5}}>
        Theo dõi log từ mọi server, để Jev quyết định sự cố nào cần người xem, LLM tìm nguyên nhân, rồi gửi tới Telegram,
        Slack, Discord, Teams hoặc agent của bạn.
      </div>
      <div style={{fontFamily: mono, fontSize: 28}}>
        <span style={{color: c.success}}>$</span> herjev run
      </div>
    </div>
    <Card edge={c.fatal} style={{position: 'absolute', right: 90, top: 300, width: 820, padding: 30}}>
      <Kicker color={c.fatal}>cảnh báo mới</Kicker>
      <div style={{height: 16}} />
      <AlertText size={19} />
    </Card>
  </Ground>
);

const Social: React.FC = () => (
  <Ground>
    <BackdropLogs rows={26} size={18} />
    <AbsoluteFill style={{background: `radial-gradient(ellipse at 50% 50%, ${c.bg} 55%, color-mix(in oklch, ${c.bg} 70%, transparent) 100%)`}} />
    <AbsoluteFill style={{padding: 80, justifyContent: 'center', gap: 34}}>
      <Wordmark size={110} />
      <Headline size={68}>Chỉ những cảnh báo thật sự quan trọng.</Headline>
      <Card edge={c.fatal} style={{padding: 26}}>
        <AlertText size={18} />
      </Card>
      <div style={{fontFamily: mono, fontSize: 24, color: c.muted}}>Jev triage · LLM root cause · mọi kênh chat</div>
    </AbsoluteFill>
  </Ground>
);

const Root: React.FC = () => (
  <>
    <Composition id="HerjevIntro" component={Intro} durationInFrames={DURATION} fps={30} width={1920} height={1080} />
    <Still id="Hero" component={Hero} width={1920} height={1080} />
    <Still id="Social" component={Social} width={1080} height={1080} />
  </>
);

registerRoot(Root);
