import { m } from "motion/react";
import { AnimatedIcon, useIconControls, type IconProps } from "./animated-icon";

function Mark() {
  const controls = useIconControls();
  return (
    <m.path
      d="M12 9v4"
      animate={controls}
      transition={{ duration: 0.4, delay: 0.2 }}
      variants={{ normal: { pathLength: 1, opacity: 1 }, animate: { pathLength: [0, 1], opacity: [0, 1] } }}
    />
  );
}

/** Lucide's triangle-alert; it shakes and redraws its stroke when it plays. */
export function AlertIcon(props: IconProps) {
  return (
    <AnimatedIcon
      {...props}
      transition={{ duration: 0.6, ease: "easeInOut" }}
      variants={{ normal: { rotate: 0 }, animate: { rotate: [0, -10, 10, -6, 6, 0] } }}
    >
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" />
      <Mark />
      <path d="M12 17h.01" />
    </AnimatedIcon>
  );
}
