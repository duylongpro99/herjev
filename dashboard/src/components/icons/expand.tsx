import { AnimatedIcon, type IconProps } from "./animated-icon";
import { Corners } from "./corners";

export function ExpandIcon(props: IconProps) {
  return (
    <AnimatedIcon {...props}>
      <Corners
        parts={[
          ["M3 16.2V21m0 0h4.8M3 21l6-6", -2, 2],
          ["M21 7.8V3m0 0h-4.8M21 3l-6 6", 2, -2],
        ]}
      />
    </AnimatedIcon>
  );
}
