// Anteater brand mark as a currentColor SVG vector (public/anteater.png is the same mark as a raster).

import type { CSSProperties } from "react";

const PATH =
  "M10 102 C8 80, 22 44, 40 38 C50 35, 56 40, 58 50 C60 58, 62 60, 66 58 " +
  "C72 54, 76 38, 90 30 C108 20, 130 20, 150 28 C186 42, 222 72, 250 106 " +
  "C252 108, 250 110, 246 107 C236 98, 214 86, 200 84 C200 92, 198 100, 196 112 " +
  "L189 112 C189 104, 188 96, 186 90 C185 96, 184 104, 184 112 L177 112 " +
  "C176 102, 168 90, 150 84 C138 80, 128 82, 124 86 C124 94, 122 102, 120 112 " +
  "L113 112 C113 104, 112 96, 110 90 C109 96, 108 104, 108 112 L101 112 " +
  "C100 102, 92 92, 76 88 C58 84, 42 92, 30 100 C22 105, 14 104, 10 102 Z";

export interface LogoProps {
  size?: number;
  style?: CSSProperties;
}

export function Logo({ size = 26, style }: LogoProps) {
  return (
    <svg
      width={(size * 260) / 138}
      height={size}
      viewBox="0 0 260 138"
      fill="currentColor"
      style={{ display: "block", flexShrink: 0, ...style }}
      aria-hidden="true"
    >
      <path d={PATH} />
    </svg>
  );
}
