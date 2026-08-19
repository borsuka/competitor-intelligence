import { Waves } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { SeverityBadge } from "@/components/intelligence";
import {
  Card,
  CardBody,
  EmptyState,
  PageHeader,
} from "@/components/ui/primitives";
import { api, serverFetch, withQuery } from "@/lib/api";
import type { Change, Competitor, Paginated } from "@/lib/types";
import { cn, formatDateTime, formatRelative, humanize } from "@/lib/utils";

export const metadata: Metadata = { title: "Monitoring" };

const SEVERITIES = [
  { value: "", label: "All" },
  { value: "medium", label: "Medium and above" },
  { value: "high", label: "High only" },
] as const;

export default async function MonitoringPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<{ severity?: string }>;
}) {
  const { orgId } = await params;
  const { severity } = await searchParams;

  const [changes, competitors] = await Promise.all([
    serverFetch<Paginated<Change>>(
      withQuery(api.org(orgId).changes, { min_severity: severity, limit: 100 }),
    ),
    serverFetch<Paginated<Competitor>>(
      withQuery(api.org(orgId).competitors, { status: "active", limit: 100 }),
    ),
  ]);

  const nameById = new Map(competitors.items.map((c) => [c.id, c.name]));

  // Group by day so the feed reads as a timeline rather than an undifferentiated list.
  const byDay = new Map<string, Change[]>();
  for (const change of changes.items) {
    const day = new Date(change.detected_at).toDateString();
    byDay.set(day, [...(byDay.get(day) ?? []), change]);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Monitoring"
        description="Meaningful changes on your competitors' sites. Cosmetic edits and rotating tokens are filtered out."
      />

      <div className="flex gap-1 rounded-[--radius-control] border border-border p-0.5 sm:w-fit">
        {SEVERITIES.map((option) => {
          const active = (severity ?? "") === option.value;
          return (
            <Link
              key={option.label}
              href={
                option.value
                  ? `/${orgId}/monitoring?severity=${option.value}`
                  : `/${orgId}/monitoring`
              }
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex-1 rounded-[calc(var(--radius-control)-2px)] px-3 py-1.5 text-center text-sm sm:flex-none",
                active
                  ? "bg-accent-soft font-medium text-accent"
                  : "text-ink-muted hover:text-ink",
              )}
            >
              {option.label}
            </Link>
          );
        })}
      </div>

      {changes.items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Waves className="size-5" aria-hidden />}
            title="Nothing has changed"
            description="Competitors are re-crawled on a schedule based on how important you marked them. Changes show up here once there is a previous crawl to compare against."
          />
        </Card>
      ) : (
        <div className="space-y-6">
          {[...byDay.entries()].map(([day, dayChanges]) => (
            <section key={day}>
              <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle">
                {day}
              </h2>
              <Card>
                <CardBody className="p-0">
                  <ul className="divide-y divide-border">
                    {dayChanges.map((change) => (
                      <li key={change.id} className="px-5 py-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <Link
                              href={`/${orgId}/competitors/${change.competitor_id}`}
                              className="text-sm font-medium text-ink hover:text-accent"
                            >
                              {change.title}
                            </Link>
                            {change.description ? (
                              <p className="mt-1 text-sm text-ink-muted">
                                {change.description}
                              </p>
                            ) : null}

                            {/* Before/after is the whole point of the feature, so it is
                                shown inline rather than behind a click. */}
                            {change.before && change.after ? (
                              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                                <span className="rounded bg-surface-raised px-2 py-1 font-mono text-ink-muted line-through">
                                  {summarise(change.before)}
                                </span>
                                <span className="text-ink-subtle" aria-hidden>
                                  →
                                </span>
                                <span className="rounded bg-accent-soft px-2 py-1 font-mono text-accent">
                                  {summarise(change.after)}
                                </span>
                              </div>
                            ) : null}

                            <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-subtle">
                              <span>{nameById.get(change.competitor_id) ?? "Competitor"}</span>
                              <span aria-hidden>·</span>
                              <span>{humanize(change.change_type)}</span>
                              <span aria-hidden>·</span>
                              <time dateTime={change.detected_at} title={formatDateTime(change.detected_at)}>
                                {formatRelative(change.detected_at)}
                              </time>
                            </p>
                          </div>
                          <SeverityBadge severity={change.severity} />
                        </div>
                      </li>
                    ))}
                  </ul>
                </CardBody>
              </Card>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

/** Render a before/after payload compactly — a price, a name, or a short JSON tail. */
function summarise(payload: Record<string, unknown>): string {
  if ("amount" in payload) {
    const currency = typeof payload.currency === "string" ? `${payload.currency} ` : "";
    return payload.amount === null ? "Custom pricing" : `${currency}${String(payload.amount)}`;
  }
  if (typeof payload.name === "string") return payload.name;
  if (typeof payload.positioning === "string") {
    return payload.positioning.slice(0, 70) + (payload.positioning.length > 70 ? "…" : "");
  }
  if (typeof payload.url === "string") return payload.url;
  return JSON.stringify(payload).slice(0, 70);
}
