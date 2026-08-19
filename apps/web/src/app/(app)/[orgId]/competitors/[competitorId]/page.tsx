import {
  ExternalLink,
  Megaphone,
  Package,
  Search,
  Tag as TagIcon,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AnalyzeButton, JobProgress } from "@/components/competitor-actions";
import {
  ArchiveCompetitorButton,
  CompetitorSettings,
  DeleteCompetitorButton,
} from "@/components/competitor-settings";
import {
  DataNotes,
  Favicon,
  InjectionNotice,
  MockProviderNotice,
  ProvenanceBadge,
  ScoreBreakdown,
  ScoreRing,
  SeverityBadge,
  Tag,
  ThreatBadge,
} from "@/components/intelligence";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  Callout,
  EmptyState,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { ApiClientError, api, serverFetch, withQuery } from "@/lib/api";
import type { Change, CompetitorDetail, Paginated } from "@/lib/types";
import {
  displayUrl,
  formatConfidence,
  formatDateTime,
  formatPrice,
  formatRelative,
  humanize,
} from "@/lib/utils";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ orgId: string; competitorId: string }>;
}): Promise<Metadata> {
  const { orgId, competitorId } = await params;
  try {
    const detail = await serverFetch<CompetitorDetail>(
      api.org(orgId).competitor(competitorId),
    );
    return { title: detail.competitor.name };
  } catch {
    return { title: "Competitor" };
  }
}

export default async function CompetitorDetailPage({
  params,
}: {
  params: Promise<{ orgId: string; competitorId: string }>;
}) {
  const { orgId, competitorId } = await params;

  let detail: CompetitorDetail;
  try {
    detail = await serverFetch<CompetitorDetail>(api.org(orgId).competitor(competitorId));
  } catch (error) {
    if (error instanceof ApiClientError && error.isNotFound) notFound();
    throw error;
  }

  const changes = await serverFetch<Paginated<Change>>(
    withQuery(api.org(orgId).competitorChanges(competitorId), { limit: 15 }),
  );

  const { competitor, analysis, score, products, pricing, pages, running_job } = detail;
  const observedPricing = pricing.filter((plan) => plan.amount !== null);

  return (
    <div className="space-y-6">
      {/* ------------------------------------------------------------- header */}
      <div className="flex flex-col gap-4">
        <Link
          href={`/${orgId}/competitors`}
          className="text-sm text-ink-muted hover:text-ink"
        >
          ← All competitors
        </Link>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <Favicon url={competitor.favicon_url} name={competitor.name} size={40} />
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold tracking-tight text-ink">
                {competitor.name}
              </h1>
              <a
                href={competitor.website_url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="mt-0.5 inline-flex items-center gap-1 text-sm text-ink-muted hover:text-accent"
              >
                {displayUrl(competitor.website_url)}
                <ExternalLink className="size-3" aria-hidden />
              </a>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <ThreatBadge level={score?.threat_level ?? "unknown"} />
                {competitor.category ? <Badge variant="outline">{competitor.category}</Badge> : null}
                <span className="text-xs text-ink-subtle">
                  Last analysed {formatRelative(competitor.last_analyzed_at)}
                </span>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-start gap-2">
            <CompetitorSettings orgId={orgId} competitor={competitor} />
            <ArchiveCompetitorButton orgId={orgId} competitor={competitor} />
            <DeleteCompetitorButton orgId={orgId} competitor={competitor} />
            <AnalyzeButton
              orgId={orgId}
              competitorId={competitorId}
              disabled={Boolean(running_job)}
            />
          </div>
        </div>
      </div>

      {competitor.status === "archived" ? (
        <Callout tone="neutral" title="This competitor is archived">
          Scheduled crawls are paused and no spend accrues. Their history is intact —
          restore them to resume monitoring.
        </Callout>
      ) : null}

      {running_job ? <JobProgress orgId={orgId} job={running_job} /> : null}
      {analysis?.is_mock ? <MockProviderNotice /> : null}
      {analysis ? <InjectionNotice flags={analysis.injection_flags} /> : null}
      {analysis && analysis.data_notes.length > 0 ? (
        <DataNotes notes={analysis.data_notes} />
      ) : null}

      {!analysis && !running_job ? (
        <Card>
          <EmptyState
            title="Not analysed yet"
            description="Run an analysis to crawl this competitor's public pages and build their profile."
          />
        </Card>
      ) : null}

      {analysis ? (
        <div className="grid gap-6 lg:grid-cols-3">
          {/* ------------------------------------------------------ summary */}
          <div className="space-y-6 lg:col-span-2">
            <Card>
              <CardHeader
                title="Overview"
                description="Generated by the AI from the crawled pages."
                action={<ProvenanceBadge source="ai_inference" />}
              />
              <CardBody className="space-y-4">
                <p className="text-sm leading-relaxed text-ink-muted">{analysis.summary}</p>

                {analysis.positioning ? (
                  <div>
                    <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                      Positioning
                    </h3>
                    <p className="mt-1 text-sm text-ink-muted">{analysis.positioning}</p>
                  </div>
                ) : null}

                {analysis.target_audience.length > 0 ? (
                  <div>
                    <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                      Target audience
                    </h3>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {analysis.target_audience.map((audience) => (
                        <Tag key={audience}>{audience}</Tag>
                      ))}
                    </div>
                  </div>
                ) : null}

                {analysis.value_propositions.length > 0 ? (
                  <div>
                    <h3 className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                      Value propositions
                    </h3>
                    <ul className="mt-1.5 space-y-1 text-sm text-ink-muted">
                      {analysis.value_propositions.map((value) => (
                        <li key={value} className="flex gap-2">
                          <span className="mt-1.5 size-1 shrink-0 rounded-full bg-ink-subtle" />
                          {value}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </CardBody>
            </Card>

            {/* -------------------------------------------------- pricing */}
            <Card>
              <CardHeader
                title="Pricing"
                description={
                  observedPricing.length > 0
                    ? "Amounts were read directly from the competitor's pages."
                    : "No published prices were found on the crawled pages."
                }
                action={
                  observedPricing.length > 0 ? <ProvenanceBadge source="observed" /> : null
                }
              />
              <CardBody className="p-0">
                {pricing.length === 0 ? (
                  <EmptyState
                    icon={<TagIcon className="size-5" aria-hidden />}
                    title="No pricing plans identified"
                    description={
                      // The notes explain what became of any prices that were found, so a
                      // blank section does not read as a failed crawl.
                      analysis.data_notes[0] ??
                      "This competitor may not publish plan pricing, or the pricing page was not reachable."
                    }
                  />
                ) : (
                  <Table caption={`Pricing plans for ${competitor.name}`}>
                    <thead>
                      <tr>
                        <Th>Plan</Th>
                        <Th className="text-right">Price</Th>
                        <Th className="hidden sm:table-cell">Billing</Th>
                        <Th className="hidden md:table-cell">Source</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {pricing.map((plan) => (
                        <tr key={plan.id}>
                          <Td>
                            <span className="font-medium text-ink">{plan.name}</span>
                            {plan.features.length > 0 ? (
                              <span className="mt-0.5 block text-xs text-ink-subtle">
                                {plan.features.slice(0, 3).join(" · ")}
                              </span>
                            ) : null}
                          </Td>
                          <Td className="tabular text-right font-semibold">
                            {formatPrice(plan.amount, plan.currency, {
                              custom: plan.is_custom_pricing,
                              free: plan.is_free,
                            })}
                          </Td>
                          <Td className="hidden text-ink-muted sm:table-cell">
                            {plan.billing_period === "unknown"
                              ? "Not stated"
                              : humanize(plan.billing_period)}
                          </Td>
                          <Td className="hidden md:table-cell">
                            <ProvenanceBadge source={plan.source} />
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
              </CardBody>
            </Card>

            {/* ------------------------------------------------- products */}
            <Card>
              <CardHeader
                title="Products"
                action={products.length > 0 ? <ProvenanceBadge source="ai_inference" /> : null}
              />
              <CardBody className="p-0">
                {products.length === 0 ? (
                  <EmptyState
                    icon={<Package className="size-5" aria-hidden />}
                    title="No products identified"
                    description="Nothing on the crawled pages looked like a distinct product or service."
                  />
                ) : (
                  <ul className="divide-y divide-border">
                    {products.map((product) => (
                      <li key={product.id} className="px-5 py-4">
                        <div className="flex items-baseline justify-between gap-3">
                          <h3 className="text-sm font-medium text-ink">{product.name}</h3>
                          {product.category ? (
                            <Badge variant="outline">{product.category}</Badge>
                          ) : null}
                        </div>
                        {product.description ? (
                          <p className="mt-1 text-sm text-ink-muted">{product.description}</p>
                        ) : null}
                        {product.features.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {product.features.slice(0, 8).map((feature) => (
                              <Tag key={feature}>{feature}</Tag>
                            ))}
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

            {/* ----------------------------------- strengths and weaknesses */}
            <div className="grid gap-6 sm:grid-cols-2">
              <Card>
                <CardHeader title="Strengths" action={<ProvenanceBadge source="ai_inference" />} />
                <CardBody>
                  {analysis.strengths.length === 0 ? (
                    <p className="text-sm text-ink-subtle">
                      Nothing on the site supported a specific strength.
                    </p>
                  ) : (
                    <ul className="space-y-3">
                      {analysis.strengths.map((insight) => (
                        <li key={insight.title} className="flex gap-2.5">
                          <ThumbsUp className="mt-0.5 size-4 shrink-0 text-low" aria-hidden />
                          <div>
                            <p className="text-sm font-medium text-ink">{insight.title}</p>
                            <p className="mt-0.5 text-sm text-ink-muted">{insight.detail}</p>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Weaknesses" action={<ProvenanceBadge source="ai_inference" />} />
                <CardBody>
                  {analysis.weaknesses.length === 0 ? (
                    <p className="text-sm text-ink-subtle">
                      Nothing on the site supported a specific weakness.
                    </p>
                  ) : (
                    <ul className="space-y-3">
                      {analysis.weaknesses.map((insight) => (
                        <li key={insight.title} className="flex gap-2.5">
                          <ThumbsDown
                            className="mt-0.5 size-4 shrink-0 text-moderate"
                            aria-hidden
                          />
                          <div>
                            <p className="text-sm font-medium text-ink">{insight.title}</p>
                            <p className="mt-0.5 text-sm text-ink-muted">{insight.detail}</p>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardBody>
              </Card>
            </div>

            {/* ------------------------------------------ recommendations */}
            <Card>
              <CardHeader
                title="What you could do about it"
                description="Recommendations for your company, based on this competitor."
                action={
                  analysis.recommendations.length > 0 ? (
                    <ProvenanceBadge source="ai_inference" />
                  ) : null
                }
              />
              <CardBody>
                {analysis.recommendations.length === 0 ? (
                  <Callout tone="neutral">
                    Recommendations need to know about your own company. Add a description in{" "}
                    <Link
                      href={`/${orgId}/settings`}
                      className="font-medium text-accent hover:underline"
                    >
                      settings
                    </Link>{" "}
                    and the next analysis will produce them. Generic advice would not be
                    worth reading.
                  </Callout>
                ) : (
                  <ul className="space-y-4">
                    {analysis.recommendations.map((recommendation) => (
                      <li key={recommendation.title}>
                        <div className="flex items-baseline justify-between gap-3">
                          <p className="text-sm font-medium text-ink">
                            {recommendation.title}
                          </p>
                          <span className="flex shrink-0 gap-1.5">
                            <Badge variant="outline">{recommendation.priority} priority</Badge>
                            <Badge variant="outline">{recommendation.effort} effort</Badge>
                          </span>
                        </div>
                        <p className="mt-1 text-sm text-ink-muted">
                          {recommendation.rationale}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          </div>

          {/* --------------------------------------------------- sidebar */}
          <div className="space-y-6">
            <Card>
              <CardHeader title="Competitive score" />
              <CardBody className="space-y-5">
                <div className="flex justify-center">
                  <ScoreRing score={score?.overall ?? null} />
                </div>
                {score ? (
                  <ScoreBreakdown
                    dimensions={score.dimensions}
                    confidence={score.confidence}
                    methodologyVersion={score.methodology_version}
                  />
                ) : (
                  <p className="text-sm text-ink-subtle">
                    No score has been computed for this competitor yet.
                  </p>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Recent changes" />
              <CardBody className="p-0">
                {changes.items.length === 0 ? (
                  <EmptyState
                    title="No changes yet"
                    description="Changes appear after the second analysis, once there is something to compare against."
                  />
                ) : (
                  <ul className="divide-y divide-border">
                    {changes.items.map((change) => (
                      <li key={change.id} className="px-5 py-3">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm leading-snug text-ink">{change.title}</p>
                          <SeverityBadge severity={change.severity} />
                        </div>
                        <p className="mt-1 text-xs text-ink-subtle">
                          {humanize(change.change_type)} ·{" "}
                          <time dateTime={change.detected_at}>
                            {formatRelative(change.detected_at)}
                          </time>
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Marketing" action={<ProvenanceBadge source="ai_inference" />} />
              <CardBody>
                {analysis.marketing_channels.length === 0 ? (
                  <p className="text-sm text-ink-subtle">
                    No marketing channels were visible from the crawled pages.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {analysis.marketing_channels.map((channel) => (
                      <Tag key={channel}>
                        <Megaphone className="size-3" aria-hidden />
                        {channel}
                      </Tag>
                    ))}
                  </div>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Pages crawled"
                description={`${pages.length} page${pages.length === 1 ? "" : "s"} in the latest crawl`}
                action={<ProvenanceBadge source="observed" />}
              />
              <CardBody className="p-0">
                {pages.length === 0 ? (
                  <EmptyState
                    icon={<Search className="size-5" aria-hidden />}
                    title="No pages recorded"
                    description="Nothing was successfully fetched from this site."
                  />
                ) : (
                  <ul className="max-h-72 divide-y divide-border overflow-y-auto">
                    {pages.map((page) => (
                      <li key={page.id} className="px-5 py-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm text-ink">
                            {page.title ?? displayUrl(page.url)}
                          </span>
                          <Badge variant="outline">{humanize(page.page_type)}</Badge>
                        </div>
                        <span className="mt-0.5 block truncate font-mono text-xs text-ink-subtle">
                          {displayUrl(page.url)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Analysis details" />
              <CardBody className="space-y-2 text-sm">
                <Row label="Provider" value={analysis.is_mock ? "Development (mock)" : analysis.provider} />
                <Row label="Model" value={analysis.model} />
                <Row label="Depth" value={humanize(analysis.depth)} />
                <Row label="Pages analysed" value={String(analysis.pages_analyzed)} />
                <Row label="Confidence" value={formatConfidence(analysis.confidence)} />
                <Row label="Run at" value={formatDateTime(analysis.created_at)} />
                <Row
                  label="Tokens"
                  value={`${analysis.tokens_in.toLocaleString()} in / ${analysis.tokens_out.toLocaleString()} out`}
                />
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-subtle">{label}</dt>
      <dd className="truncate text-right text-ink">{value}</dd>
    </div>
  );
}
