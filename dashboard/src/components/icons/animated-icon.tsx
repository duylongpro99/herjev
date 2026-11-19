// Shared shell for the animated icons: geometry and motion come from lucide-animated
// (MIT, pqoqubbw/icons), drawn on lucide's 24px grid and inheriting the text colour.
import { domAnimation, LazyMotion, m, useAnimationControls, useReducedMotion, type LegacyAnimationControls, type Transition, type Variants } from "motion/react";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";

const Controls = createContext<LegacyAnimationControls | null>(null);

/** Controls for the animated parts inside an icon; they follow the shell's state. */
export function useIconControls(): LegacyAnimationControls {
  const controls = useContext(Controls);
  if (!controls) throw new Error("useIconControls must be used inside AnimatedIcon");
  return controls;
}

export interface IconProps {
  size?: number;
  title?: string;
  className?: string;
  /** Plays the icon's state animation (or its looping "spin" variant) while true. */
  play?: boolean;
}

interface ShellProps extends IconProps {
  /** Motion for the whole glyph; parts animate themselves through useIconControls. */
  variants?: Variants;
  transition?: Transition;
  /** Plays the looping "spin" variant instead of "animate" while `play` is true. */
  loop?: boolean;
  children: ReactNode;
}

// The control an icon sits in triggers it, so a hover anywhere on the button counts.
const HOST = "button, a, label, [role=option], .search, .pal-input-wrap";

export function AnimatedIcon({ size = 14, title, className, play = false, loop = false, variants, transition, children }: ShellProps) {
  const controls = useAnimationControls();
  const reduce = useReducedMotion();
  const ref = useRef<SVGSVGElement>(null);
  const playing = useRef(play);
  playing.current = play;

  useEffect(() => {
    if (reduce) return;
    if (play) controls.start(loop ? "spin" : "animate");
    else controls.start("normal");
  }, [play, loop, reduce, controls]);

  useEffect(() => {
    const svg = ref.current;
    const host = svg?.closest<HTMLElement>(HOST) ?? svg?.parentElement;
    if (!host || reduce) return;
    const start = () => !playing.current && controls.start("animate");
    const stop = () => !playing.current && controls.start("normal");
    const blur = (e: FocusEvent) => !host.contains(e.relatedTarget as Node | null) && stop();
    host.addEventListener("pointerenter", start);
    host.addEventListener("pointerleave", stop);
    host.addEventListener("focusin", start);
    host.addEventListener("focusout", blur);
    return () => {
      host.removeEventListener("pointerenter", start);
      host.removeEventListener("pointerleave", stop);
      host.removeEventListener("focusin", start);
      host.removeEventListener("focusout", blur);
    };
  }, [controls, reduce]);

  return (
    <LazyMotion features={domAnimation}>
      <Controls.Provider value={controls}>
        <m.svg
          ref={ref}
          className={className ? `icon ${className}` : "icon"}
          width={size}
          height={size}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden={title ? undefined : true}
          role={title ? "img" : undefined}
          initial="normal"
          animate={controls}
          variants={variants}
          transition={transition}
        >
          {title && <title>{title}</title>}
          {children}
        </m.svg>
      </Controls.Provider>
    </LazyMotion>
  );
}
