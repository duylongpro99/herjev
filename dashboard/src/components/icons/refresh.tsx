import { AnimatedIcon, type IconProps } from "./animated-icon";

/** Turns a little on hover and spins for as long as `play` holds. */
export function RefreshIcon(props: IconProps) {
  return (
    <AnimatedIcon
      {...props}
      loop
      transition={{ type: "spring", stiffness: 250, damping: 25 }}
      variants={{
        normal: { rotate: 0 },
        animate: { rotate: 50 },
        spin: { rotate: [0, 360], transition: { duration: 0.7, ease: "linear", repeat: Infinity } },
      }}
    >
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </AnimatedIcon>
  );
}
