import type { ReactNode } from "react";

/* ---------- PageHeader ---------- */
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow ? (
          <div
            className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em]"
            style={{ color: "var(--fg-subtle)" }}
          >
            {eyebrow}
          </div>
        ) : null}
        <h1
          className="display text-3xl font-bold tracking-tight"
          style={{ color: "var(--fg)" }}
        >
          {title}
        </h1>
        {subtitle ? (
          <p
            className="mt-1 max-w-2xl text-sm"
            style={{ color: "var(--fg-muted)" }}
          >
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </header>
  );
}

/* ---------- StatCard ---------- */
type Tone = "default" | "success" | "warning" | "danger" | "info" | "accent";

const toneChipClass: Record<Tone, string> = {
  default: "chip-neutral",
  success: "chip-success",
  warning: "chip-warning",
  danger: "chip-danger",
  info: "chip-info",
  accent: "chip-accent",
};

export function StatCard({
  label,
  value,
  unit,
  hint,
  tone = "default",
  trend,
  icon,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  trend?: { delta: string; direction: "up" | "down" | "flat" };
  icon?: ReactNode;
}) {
  return (
    <div className="card card-hover relative overflow-hidden p-5">
      <div className="flex items-start justify-between gap-2">
        <div
          className="text-[11px] font-semibold uppercase tracking-[0.1em]"
          style={{ color: "var(--fg-muted)" }}
        >
          {label}
        </div>
        {icon ? (
          <div
            className="flex h-8 w-8 items-center justify-center rounded-lg"
            style={{
              background: "var(--accent-soft)",
              color: "var(--accent-strong)",
            }}
          >
            {icon}
          </div>
        ) : null}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <div
          className="display text-4xl font-bold"
          style={{ color: "var(--fg)" }}
        >
          {value}
        </div>
        {unit ? (
          <div
            className="text-sm font-medium"
            style={{ color: "var(--fg-muted)" }}
          >
            {unit}
          </div>
        ) : null}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <div
          className="text-xs"
          style={{ color: "var(--fg-muted)" }}
        >
          {hint ?? ""}
        </div>
        {trend ? (
          <span
            className={`chip ${
              trend.direction === "up"
                ? "chip-success"
                : trend.direction === "down"
                ? "chip-danger"
                : "chip-neutral"
            }`}
          >
            {trend.direction === "up"
              ? "▲"
              : trend.direction === "down"
              ? "▼"
              : "→"}{" "}
            {trend.delta}
          </span>
        ) : null}
      </div>
      <div
        className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full opacity-[0.08]"
        style={{ background: "var(--accent)" }}
      />
      <div
        aria-hidden
        className={`pointer-events-none absolute inset-x-0 bottom-0 h-1 ${toneChipClass[tone]}`}
        style={{ opacity: 0.6 }}
      />
    </div>
  );
}

/* ---------- StatusBadge / Chip ---------- */
export function StatusBadge({
  tone = "default",
  dot = true,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  const cls: Record<Tone, string> = {
    default: "chip-neutral",
    success: "chip-success",
    warning: "chip-warning",
    danger: "chip-danger",
    info: "chip-info",
    accent: "chip-accent",
  };
  return (
    <span className={`chip ${cls[tone]} ${dot ? "chip-dot" : ""}`}>
      {children}
    </span>
  );
}

/* ---------- Section ---------- */
export function Section({
  title,
  action,
  children,
  padded = true,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  padded?: boolean;
}) {
  return (
    <section className="card">
      {title || action ? (
        <div
          className="flex items-center justify-between border-b px-5 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <h3 className="text-sm font-semibold" style={{ color: "var(--fg)" }}>
            {title}
          </h3>
          {action ? <div>{action}</div> : null}
        </div>
      ) : null}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

/* ---------- Sparkline ---------- */
export function Sparkline({
  data,
  height = 36,
  width = 120,
  stroke = "var(--accent)",
  fill = "color-mix(in srgb, var(--accent) 18%, transparent)",
}: {
  data: number[];
  height?: number;
  width?: number;
  stroke?: string;
  fill?: string;
}) {
  if (!data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1 || 1);
  const points = data.map((d, i) => {
    const x = i * step;
    const y = height - ((d - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  });
  const linePath = "M" + points.join(" L");
  const areaPath = `${linePath} L${width},${height} L0,${height} Z`;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="block"
    >
      <path d={areaPath} fill={fill} />
      <path d={linePath} fill="none" stroke={stroke} strokeWidth={1.8} />
    </svg>
  );
}

/* ---------- Donut ---------- */
export function Donut({
  segments,
  size = 140,
  thickness = 14,
  centerLabel,
  centerSub,
}: {
  segments: { value: number; color: string; label?: string }[];
  size?: number;
  thickness?: number;
  centerLabel?: ReactNode;
  centerSub?: ReactNode;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const radius = size / 2 - thickness / 2 - 2;
  const circ = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="block -rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="var(--bg-subtle)"
          strokeWidth={thickness}
          fill="none"
        />
        {segments.map((seg, i) => {
          const length = (seg.value / total) * circ;
          const dash = `${length} ${circ - length}`;
          const dashoffset = -offset;
          offset += length;
          return (
            <circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              stroke={seg.color}
              strokeWidth={thickness}
              fill="none"
              strokeDasharray={dash}
              strokeDashoffset={dashoffset}
              strokeLinecap="butt"
            />
          );
        })}
      </svg>
      {(centerLabel || centerSub) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
          <div
            className="display text-2xl font-bold"
            style={{ color: "var(--fg)" }}
          >
            {centerLabel}
          </div>
          <div className="text-[11px]" style={{ color: "var(--fg-muted)" }}>
            {centerSub}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- EmptyState ---------- */
export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      className="flex flex-col items-center rounded-[var(--r-lg)] border border-dashed p-10 text-center"
      style={{
        borderColor: "var(--border-strong)",
        background: "var(--panel-alt)",
      }}
    >
      {icon ? (
        <div
          className="mb-3 flex h-10 w-10 items-center justify-center rounded-full"
          style={{
            background: "var(--bg-subtle)",
            color: "var(--fg-muted)",
          }}
        >
          {icon}
        </div>
      ) : null}
      <div className="font-semibold" style={{ color: "var(--fg)" }}>
        {title}
      </div>
      {children ? (
        <div
          className="mt-1 max-w-md text-sm"
          style={{ color: "var(--fg-muted)" }}
        >
          {children}
        </div>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/* ---------- KpiGrid helper ---------- */
export function KpiGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">{children}</div>
  );
}
