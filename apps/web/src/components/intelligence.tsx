/**
 * Domain display components.
 *
 * These carry the product's trust model into the interface: a score that could not be
 * computed reads "Insufficient data", an AI-derived claim is visually distinct from an
 * observed one, and development output is labelled wherever it appears.
 */

import { Bot, CircleAlert, Eye, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Badge, Callout } from "@/components/ui/primitives";
import type { DataSource, ScoreDimension, Severity, ThreatLevel } from "@/lib/types";
import {
  cn,
  formatConfidence,
  formatScore,
  humanize,
  provenanceStyle,
  scoreLabel,
  severityStyle,
  threatStyle,
} from "@/lib/utils";

export function ThreatBadge({ level }: { level: ThreatLevel | string | null | undefined }) {
  const style = threatStyle(level);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
        style.className,
      )}
    >
      {/* A dot alone would be meaningless to anyone who cannot separate the hues, so the
          label always travels with it. */}
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {style.label}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: Severity | string }) {
  const style = severityStyle(severity);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        style.className,
      )}
    >
      {style.label}
    </span>
  );
}

export function ProvenanceBadge({ source }: { source: DataSource | string }) {
  const style = provenanceStyle(source);
  const Icon = source === "observed" ? Eye : Bot;
  return (
    <span
      title={style.title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
        style.className,
      )}
    >
      <Icon className="size-3" aria-hidden />
      {style.label}
    </span>
  );
}

/**
 * Shown wherever development-provider output is displayed.
 *
 * Deliberately prominent and not dismissible: a user must never mistake heuristic
 * placeholder text for real competitive intelligence.
 */
export function MockProviderNotice({ className }: { className?: string }) {
  return (
    <div className={className}>
      <Callout
        tone="warning"
        title="Development AI provider"
        icon={<CircleAlert className="size-4" aria-hidden />}
      >
        No AI provider key is configured, so analyses on this page were produced by the
        built-in development provider. It restates what the crawler observed and does not
        interpret anything. Set <code className="font-mono text-xs">ANTHROPIC_API_KEY</code>{" "}
        to generate real analysis.
      </Callout>
    </div>
  );
}

export function InjectionNotice({
  flags,
}: {
  flags: { url: string; patterns: string[] }[];
}) {
  if (flags.length === 0) return null;

  return (
    <Callout
      tone="danger"
      title="This site contains text aimed at AI systems"
      icon={<ShieldAlert className="size-4" aria-hidden />}
    >
      <p>
        {flags.length === 1 ? "One page" : `${flags.length} pages`} on this competitor&apos;s
        site contained instruction-like text. It was treated as data and could not affect
        the analysis, but it is worth knowing about.
      </p>
      <ul className="mt-2 space-y-1">
        {flags.slice(0, 3).map((flag) => (
          <li key={flag.url} className="truncate font-mono text-xs">
            {flag.url} — {flag.patterns.join(", ")}
          </li>
        ))}
      </ul>
    </Callout>
  );
}

/**
 * The headline score.
 *
 * A ring rather than a bar, because the number is the point and the ring is only there
 * to give it weight. `null` renders as "—" with an explicit caption underneath.
 */
export function ScoreRing({
  score,
  size = 96,
  label = "Overall score",
}: {
  score: number | null;
  size?: number;
  label?: string;
}) {
  const radius = (size - 10) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = score === null ? 0 : (score / 100) * circumference;

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label}: ${scoreLabel(score)}`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={6}
          />
          {score !== null ? (
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke="var(--color-accent)"
              strokeWidth={6}
              strokeLinecap="round"
              strokeDasharray={`${progress} ${circumference}`}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
            />
          ) : null}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="tabular text-2xl font-semibold text-ink">{formatScore(score)}</span>
          {score !== null ? (
            <span className="text-[11px] text-ink-subtle">/ 100</span>
          ) : null}
        </div>
      </div>
      {score === null ? (
        <span className="text-xs text-ink-subtle">Insufficient data</span>
      ) : null}
    </div>
  );
}

/**
 * Per-dimension breakdown.
 *
 * Every row shows its rationale, so "why 82?" is answerable without leaving the page.
 * That is the difference between a score a user trusts and a number they ignore.
 */
export function ScoreBreakdown({
  dimensions,
  confidence,
  methodologyVersion,
}: {
  dimensions: Record<string, ScoreDimension>;
  confidence?: number | null;
  methodologyVersion?: string;
}) {
  const entries = Object.entries(dimensions).sort(([, a], [, b]) => {
    // Measured dimensions first, highest score at the top; unmeasured ones at the end.
    if (a.score === null && b.score === null) return 0;
    if (a.score === null) return 1;
    if (b.score === null) return -1;
    return b.score - a.score;
  });

  return (
    <div>
      <ul className="divide-y divide-border">
        {entries.map(([name, dimension]) => (
          <li key={name} className="py-3 first:pt-0 last:pb-0">
            <div className="flex items-baseline justify-between gap-4">
              <span className="text-sm font-medium text-ink">{humanize(name)}</span>
              {dimension.score === null ? (
                <span className="text-xs text-ink-subtle">Insufficient data</span>
              ) : (
                <span className="tabular text-sm font-semibold text-ink">
                  {formatScore(dimension.score)}
                </span>
              )}
            </div>

            {dimension.score !== null ? (
              <div
                className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
                role="img"
                aria-label={`${humanize(name)}: ${scoreLabel(dimension.score)}`}
              >
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${dimension.score}%` }}
                />
              </div>
            ) : null}

            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
              {dimension.rationale}
            </p>
          </li>
        ))}
      </ul>

      <p className="mt-4 border-t border-border pt-3 text-xs text-ink-subtle">
        Scores are computed from data observed on the competitor&apos;s public website and
        rounded to the nearest 5. Dimensions with no supporting data are excluded from the
        overall score rather than counted as zero.
        {confidence !== undefined && confidence !== null
          ? ` Confidence in this analysis: ${formatConfidence(confidence)}.`
          : ""}
        {methodologyVersion ? ` Methodology ${methodologyVersion}.` : ""}
      </p>
    </div>
  );
}

export function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "critical";
}) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">{label}</p>
      <p
        className={cn(
          "tabular mt-1 text-2xl font-semibold",
          tone === "critical" ? "text-critical" : "text-ink",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-xs text-ink-muted">{hint}</p> : null}
    </div>
  );
}

export function Favicon({
  url,
  name,
  size = 20,
}: {
  url: string | null;
  name: string;
  size?: number;
}) {
  if (!url) {
    return (
      <span
        className="flex shrink-0 items-center justify-center rounded bg-surface-raised text-[10px] font-semibold text-ink-subtle"
        style={{ width: size, height: size }}
        aria-hidden
      >
        {name.charAt(0).toUpperCase()}
      </span>
    );
  }

  return (
    // A plain <img>: these are third-party favicons of arbitrary size and reliability,
    // and a broken one should degrade quietly rather than fail an optimiser request.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      className="shrink-0 rounded"
      style={{ width: size, height: size }}
    />
  );
}

export function DataNotes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;

  return (
    <Callout tone="neutral" title="Corrections applied to this analysis">
      <ul className="list-inside list-disc space-y-1">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </Callout>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return <Badge variant="outline">{children}</Badge>;
}
