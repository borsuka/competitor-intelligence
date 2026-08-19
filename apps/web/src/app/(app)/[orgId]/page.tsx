import {
  ArrowUpRight,
  Activity,
  Lightbulb,
  Radar,
  TriangleAlert,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import {
  Favicon,
  Metric,
  MockProviderNotice,
  SeverityBadge,
  ThreatBadge,
} from "@/components/intelligence";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  PageHeader,
} from "@/components/ui/primitives";
import { api, serverFetch } from "@/lib/api";
import type { DashboardOverview } from "@/lib/types";
import { formatRelative, formatScore, humanize } from "@/lib/utils";

export const metadata: Metadata = { title: "Overview" };

export default async function OverviewPage({
  params,
}: {
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;
  const data = await serverFetch<DashboardOverview>(api.org(orgId).dashboard);

  // Nothing tracked yet: a dashboard of zeros teaches nothing, so the whole page becomes
  // one instruction.
  if (data.competitors_tracked === 0 && data.competitors_archived === 0) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Overview"
          description="Competitive intelligence across everyone you track."
        />
        <Card>
          <EmptyState
            icon={<Radar className="size-5" aria-hidden />}
            title="Add your first competitor"
            description="Paste a competitor's website URL. Sentinel crawls their public pages, extracts their products and pricing, scores them, and watches for changes."
            action={
              <Link href={`/${orgId}/competitors`}>
                <Button>Add a competitor</Button>
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Competitive intelligence across everyone you track."
        actions={
          <Link href={`/${orgId}/competitors`}>
            <Button>Add competitor</Button>
          </Link>
        }
      />

      {data.uses_mock_ai ? <MockProviderNotice /> : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardBody>
            <Metric
              label="Competitors"
              value={data.competitors_tracked}
              hint={`${data.competitors_analyzed} analysed`}
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                Threat level
              </p>
              <div className="mt-2">
                <ThreatBadge level={data.overall_threat_level} />
              </div>
              <p className="mt-1.5 text-xs text-ink-muted">
                Highest across your tracked set
              </p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Metric
              label="Changes (7d)"
              value={data.changes_last_7_days}
              hint={
                data.high_severity_changes > 0
                  ? `${data.high_severity_changes} high severity`
                  : "Nothing severe"
              }
              tone={data.high_severity_changes > 0 ? "critical" : "default"}
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Metric
              label="Average score"
              value={formatScore(data.average_score)}
              hint={
                data.running_jobs > 0
                  ? `${data.running_jobs} analysis in progress`
                  : "Across analysed competitors"
              }
            />
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ------------------------------------------------------- landscape */}
        <Card className="lg:col-span-2">
          <CardHeader
            title="Competitive landscape"
            description="Ranked by overall score. Unanalysed competitors appear last."
            action={
              <Link
                href={`/${orgId}/competitors`}
                className="text-sm font-medium text-accent hover:underline"
              >
                All competitors
              </Link>
            }
          />
          <CardBody className="p-0">
            {data.landscape.length === 0 ? (
              <EmptyState
                title="No analyses yet"
                description="Run an analysis on a competitor to see how they compare."
              />
            ) : (
              <ul className="divide-y divide-border">
                {data.landscape.map((competitor) => (
                  <li key={competitor.id}>
                    <Link
                      href={`/${orgId}/competitors/${competitor.id}`}
                      className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-surface-raised"
                    >
                      <Favicon url={competitor.favicon_url} name={competitor.name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-ink">
                          {competitor.name}
                        </p>
                        <p className="truncate text-xs text-ink-subtle">
                          {competitor.domain}
                        </p>
                      </div>

                      <div className="hidden w-40 shrink-0 sm:block">
                        {competitor.overall !== null ? (
                          <div
                            className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
                            role="img"
                            aria-label={`Score ${formatScore(competitor.overall)} out of 100`}
                          >
                            <div
                              className="h-full rounded-full bg-accent"
                              style={{ width: `${competitor.overall}%` }}
                            />
                          </div>
                        ) : (
                          <span className="text-xs text-ink-subtle">Not analysed</span>
                        )}
                      </div>

                      <span className="tabular w-10 shrink-0 text-right text-sm font-semibold text-ink">
                        {formatScore(competitor.overall)}
                      </span>
                      <div className="hidden shrink-0 md:block">
                        <ThreatBadge level={competitor.threat_level} />
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        {/* --------------------------------------------------- recent changes */}
        <Card>
          <CardHeader
            title="Recent changes"
            action={
              <Link
                href={`/${orgId}/monitoring`}
                className="text-sm font-medium text-accent hover:underline"
              >
                All
              </Link>
            }
          />
          <CardBody className="p-0">
            {data.recent_changes.length === 0 ? (
              <EmptyState
                icon={<Activity className="size-5" aria-hidden />}
                title="No changes detected"
                description="Once a competitor changes their pricing, products or positioning, it appears here."
              />
            ) : (
              <ul className="divide-y divide-border">
                {data.recent_changes.map((change) => (
                  <li key={change.id} className="px-5 py-3">
                    <div className="flex items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm leading-snug text-ink">{change.title}</p>
                        <p className="mt-1 flex items-center gap-2 text-xs text-ink-subtle">
                          <span>{humanize(change.change_type)}</span>
                          <span aria-hidden>·</span>
                          <time dateTime={change.detected_at}>
                            {formatRelative(change.detected_at)}
                          </time>
                        </p>
                      </div>
                      <SeverityBadge severity={change.severity} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ------------------------------------------------------ opportunities */}
        <Card>
          <CardHeader
            title="Opportunities"
            description="Dimensions where the competitors we could measure are collectively weak."
          />
          <CardBody className="p-0">
            {data.opportunities.length === 0 ? (
              <EmptyState
                icon={<Lightbulb className="size-5" aria-hidden />}
                title="No clear gaps yet"
                description="Analyse at least two competitors and gaps in the market start to show up here."
              />
            ) : (
              <ul className="divide-y divide-border">
                {data.opportunities.map((opportunity) => (
                  <li key={opportunity.dimension} className="px-5 py-4">
                    <div className="flex items-baseline justify-between gap-3">
                      <p className="text-sm font-medium text-ink">{opportunity.title}</p>
                      <span className="tabular shrink-0 text-sm text-ink-muted">
                        avg {opportunity.market_average}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-ink-muted">{opportunity.detail}</p>
                    <p className="mt-1 text-xs text-ink-subtle">
                      Based on {opportunity.competitors_measured} measured competitor
                      {opportunity.competitors_measured === 1 ? "" : "s"}.
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        {/* ------------------------------------------------------- analyses */}
        <Card>
          <CardHeader title="Recent analyses" />
          <CardBody className="p-0">
            {data.recent_analyses.length === 0 ? (
              <EmptyState
                title="No analyses yet"
                description="Analyses appear here as they complete."
              />
            ) : (
              <ul className="divide-y divide-border">
                {data.recent_analyses.map((analysis) => (
                  <li key={analysis.id}>
                    <Link
                      href={`/${orgId}/competitors/${analysis.competitor_id}`}
                      className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-surface-raised"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-ink">
                          {analysis.competitor_name}
                        </p>
                        <p className="text-xs text-ink-subtle">
                          {analysis.pages_analyzed} page
                          {analysis.pages_analyzed === 1 ? "" : "s"} ·{" "}
                          <time dateTime={analysis.created_at}>
                            {formatRelative(analysis.created_at)}
                          </time>
                          {analysis.is_mock ? " · development provider" : ""}
                        </p>
                      </div>
                      <ArrowUpRight className="size-4 shrink-0 text-ink-subtle" aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      {data.failed_jobs > 0 ? (
        <Card>
          <CardBody className="flex items-center gap-3">
            <TriangleAlert className="size-4 shrink-0 text-moderate" aria-hidden />
            <p className="text-sm text-ink-muted">
              {data.failed_jobs} analysis job{data.failed_jobs === 1 ? "" : "s"} failed in the
              last week. Open the competitor to see why and retry.
            </p>
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}
