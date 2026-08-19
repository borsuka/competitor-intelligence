import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

import type { DataSource, Severity, ThreatLevel } from "@/lib/types";

/** Merge Tailwind classes so a later class wins over an earlier conflicting one. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Score display.
 *
 * `null` means the dimension could not be measured. It renders as "Insufficient data",
 * never as 0 — the difference between "we could not tell" and "they are bad at this" is
 * the difference between a useful tool and a misleading one.
 */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return "—";
  return String(Math.round(score));
}

export function scoreLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return "Insufficient data";
  return `${Math.round(score)} out of 100`;
}

export function formatConfidence(confidence: number | null | undefined): string {
  if (confidence === null || confidence === undefined) return "unknown";
  return `${Math.round(confidence * 100)}%`;
}

export function formatPrice(
  amount: string | number | null,
  currency: string | null,
  options: { custom?: boolean; free?: boolean } = {},
): string {
  if (options.free) return "Free";
  if (options.custom || amount === null) return "Custom";

  const value = typeof amount === "string" ? Number.parseFloat(amount) : amount;
  if (Number.isNaN(value)) return "Custom";

  const rendered = Number.isInteger(value) ? String(value) : value.toFixed(2);
  return currency ? `${currency} ${rendered}` : rendered;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "Never";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "3 days ago" — falls back to an absolute date beyond a month, where relative stops helping. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return "Never";

  const then = new Date(value).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);

  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 2592000) return `${Math.floor(seconds / 86400)}d ago`;
  return formatDate(value);
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat().format(value);
}

const THREAT_STYLES: Record<ThreatLevel, { label: string; className: string }> = {
  critical: { label: "Critical", className: "bg-critical-soft text-critical" },
  high: { label: "High", className: "bg-high-soft text-high" },
  moderate: { label: "Moderate", className: "bg-moderate-soft text-moderate" },
  low: { label: "Low", className: "bg-low-soft text-low" },
  unknown: { label: "Not scored", className: "bg-unknown-soft text-unknown" },
};

export function threatStyle(level: ThreatLevel | string | null | undefined) {
  return THREAT_STYLES[(level as ThreatLevel) ?? "unknown"] ?? THREAT_STYLES.unknown;
}

const SEVERITY_STYLES: Record<Severity, { label: string; className: string }> = {
  high: { label: "High", className: "bg-critical-soft text-critical" },
  medium: { label: "Medium", className: "bg-moderate-soft text-moderate" },
  low: { label: "Low", className: "bg-unknown-soft text-ink-muted" },
};

export function severityStyle(severity: Severity | string | null | undefined) {
  return SEVERITY_STYLES[(severity as Severity) ?? "low"] ?? SEVERITY_STYLES.low;
}

/**
 * Provenance labelling.
 *
 * Observed and inferred data are visually distinct everywhere they appear. This is the
 * single most important trust affordance in the product.
 */
export function provenanceStyle(source: DataSource | string | null | undefined) {
  if (source === "observed") {
    return {
      label: "Observed",
      title: "Read directly from the competitor's website.",
      className: "bg-observed-soft text-observed",
    };
  }
  return {
    label: "AI inference",
    title: "Interpreted by the AI from page content. Verify before acting on it.",
    className: "bg-inferred-soft text-inferred",
  };
}

/** Turn `price_increased` into `Price increased`. */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const spaced = value.replace(/[_-]/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

/** Strip the scheme so a URL reads as a domain in a dense table. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}
