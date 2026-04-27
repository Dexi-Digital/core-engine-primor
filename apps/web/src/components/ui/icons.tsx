import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const base = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function IconDashboard(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <rect x="3" y="3" width="7" height="9" rx="2" />
      <rect x="14" y="3" width="7" height="5" rx="2" />
      <rect x="14" y="12" width="7" height="9" rx="2" />
      <rect x="3" y="16" width="7" height="5" rx="2" />
    </svg>
  );
}
export function IconStethoscope(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M4 4v6a4 4 0 0 0 8 0V4" />
      <circle cx="18" cy="14" r="3" />
      <path d="M8 14a4 4 0 0 0 4 4c3 0 3-3 3-3" />
    </svg>
  );
}
export function IconBuilding(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h2M8 11h2M8 15h2M14 7h2M14 11h2M14 15h2M10 21v-4h4v4" />
    </svg>
  );
}
export function IconUsers(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2 20c0-3 3-5 7-5s7 2 7 5" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M15 14c3 0 6 1.5 6 4" />
    </svg>
  );
}
export function IconTruck(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <rect x="2" y="7" width="11" height="9" rx="1.5" />
      <path d="M13 10h4l3 3v3h-7z" />
      <circle cx="7" cy="18" r="1.8" />
      <circle cx="17" cy="18" r="1.8" />
    </svg>
  );
}
export function IconDollar(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M12 3v18" />
      <path d="M16 7a3 3 0 0 0-3-2h-2a3 3 0 0 0 0 6h2a3 3 0 0 1 0 6h-2a3 3 0 0 1-3-2" />
    </svg>
  );
}
export function IconDoc(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M6 3h8l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M14 3v4h4" />
      <path d="M8 13h8M8 17h6" />
    </svg>
  );
}
export function IconGavel(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M3 21h12" />
      <path d="M5 15l4-4 4 4-4 4z" />
      <path d="M11 5l4-4 4 4-4 4z" />
      <path d="M8 12l7-7" />
    </svg>
  );
}
export function IconShield(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M12 3l8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}
export function IconArrowRight(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M5 12h14" />
      <path d="M13 6l6 6-6 6" />
    </svg>
  );
}
export function IconArrowUp(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M12 19V5" />
      <path d="M6 11l6-6 6 6" />
    </svg>
  );
}
export function IconArrowDown(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M12 5v14" />
      <path d="M6 13l6 6 6-6" />
    </svg>
  );
}
export function IconBell(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8" />
      <path d="M10 21a2 2 0 0 0 4 0" />
    </svg>
  );
}
export function IconActivity(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M3 12h4l3-8 4 16 3-8h4" />
    </svg>
  );
}
export function IconBolt(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M13 2L4 14h7l-1 8 9-12h-7z" />
    </svg>
  );
}
export function IconCheck(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}
export function IconX(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}
export function IconSearch(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}
export function IconDownload(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M12 3v12" />
      <path d="M6 11l6 6 6-6" />
      <path d="M5 21h14" />
    </svg>
  );
}
export function IconRefresh(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M4 12a8 8 0 0 1 14-5.3L20 8" />
      <path d="M20 4v4h-4" />
      <path d="M20 12a8 8 0 0 1-14 5.3L4 16" />
      <path d="M4 20v-4h4" />
    </svg>
  );
}
export function IconCloud(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M7 18a5 5 0 1 1 1.5-9.8A6 6 0 0 1 20 12a4 4 0 0 1-1 6z" />
    </svg>
  );
}
export function IconMap(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M9 4l-6 2v14l6-2 6 2 6-2V4l-6 2z" />
      <path d="M9 4v14M15 6v14" />
    </svg>
  );
}
export function IconClock(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}
export function IconExternal(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M14 4h6v6" />
      <path d="M20 4l-9 9" />
      <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
    </svg>
  );
}
export function IconSettings(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}
export function IconLogout(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}
export function IconSparkles(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8z" />
      <path d="M18 15l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" />
    </svg>
  );
}
