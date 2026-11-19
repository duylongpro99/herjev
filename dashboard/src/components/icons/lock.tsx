import { m } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

function Shackle() {
  const controls = useIconControls();
  return (
    <m.path
      d="M7 11V7a5 5 0 0 1 10 0v4"
      animate={controls}
      transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
      variants={{ normal: { pathLength: 1 }, animate: { pathLength: 0.7 } }}
    />
  );
}

export function LockIcon(props: IconProps) {
  return (
    <AnimatedIcon
      {...props}
      transition={{ duration: 1, ease: [0.4, 0, 0.2, 1] }}
      variants={{ normal: { rotate: 0, scale: 1 }, animate: { rotate: [-3, 1, -2, 0], scale: [0.95, 1.05, 0.98, 1] } }}
    >
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <Shackle />
    </AnimatedIcon>
  );
}
