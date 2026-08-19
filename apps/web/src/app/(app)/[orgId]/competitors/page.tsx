import { Search, Users } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { AddCompetitorButton } from "@/components/competitor-actions";
import { Favicon, ThreatBadge } from "@/components/intelligence";
import {
  Badge,
  Card,
  CardBody,
  EmptyState,
  Input,
  PageHeader,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { api, serverFetch, withQuery } from "@/lib/api";
import type { Competitor, Paginated } from "@/lib/types";
import { formatRelative, formatScore, humanize } from "@/lib/utils";

export const metadata: Metadata = { title: "Competitors" };

const PAGE_SIZE = 25;

export default async function CompetitorsPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<{ q?: string; status?: string; page?: string }>;
}) {
  const { orgId } = await params;
  const { q, status, page } = await searchParams;

  const currentPage = Math.max(1, Number.parseInt(page ?? "1", 10) || 1);
  const offset = (currentPage - 1) * PAGE_SIZE;

  const data = await serverFetch<Paginated<Competitor>>(
    withQuery(api.org(orgId).competitors, {
      search: q,
      status: status ?? "active",
      limit: PAGE_SIZE,
      offset,
    }),
  );

  const archived = status === "archived";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Competitors"
        description="Everyone you track. Add a URL and the rest is automatic."
        actions={<AddCompetitorButton orgId={orgId} />}
      />

      {/* Filters are plain links and a GET form, so the state lives in the URL and a
          filtered view can be bookmarked or shared. */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <form className="relative flex-1" action={`/${orgId}/competitors`}>
          {archived ? <input type="hidden" name="status" value="archived" /> : null}
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-subtle"
            aria-hidden
          />
          <label htmlFor="competitor-search" className="sr-only">
            Search competitors
          </label>
          <Input
            id="competitor-search"
            name="q"
            defaultValue={q ?? ""}
            placeholder="Search by name or domain"
            className="pl-9"
          />
        </form>

        <div className="flex gap-1 rounded-[--radius-control] border border-border p-0.5">
          <Link
            href={`/${orgId}/competitors`}
            aria-current={!archived ? "page" : undefined}
            className={
              !archived
                ? "rounded-[calc(var(--radius-control)-2px)] bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent"
                : "rounded-[calc(var(--radius-control)-2px)] px-3 py-1.5 text-sm text-ink-muted hover:text-ink"
            }
          >
            Active
          </Link>
          <Link
            href={`/${orgId}/competitors?status=archived`}
            aria-current={archived ? "page" : undefined}
            className={
              archived
                ? "rounded-[calc(var(--radius-control)-2px)] bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent"
                : "rounded-[calc(var(--radius-control)-2px)] px-3 py-1.5 text-sm text-ink-muted hover:text-ink"
            }
          >
            Archived
          </Link>
        </div>
      </div>

      <Card>
        {data.items.length === 0 ? (
          <EmptyState
            icon={<Users className="size-5" aria-hidden />}
            title={q ? "No matches" : archived ? "Nothing archived" : "No competitors yet"}
            description={
              q
                ? `Nothing matches "${q}". Try a different name or domain.`
                : archived
                  ? "Competitors you archive stop being crawled but keep their history."
                  : "Add a competitor's website URL and Sentinel will crawl it, extract their products and pricing, and start watching for changes."
            }
            action={!q && !archived ? <AddCompetitorButton orgId={orgId} /> : undefined}
          />
        ) : (
          <>
            <Table caption="Tracked competitors with their latest score and threat level">
              <thead>
                <tr>
                  <Th>Competitor</Th>
                  <Th className="hidden sm:table-cell">Category</Th>
                  <Th className="text-right">Score</Th>
                  <Th className="hidden md:table-cell">Threat</Th>
                  <Th className="hidden lg:table-cell">Importance</Th>
                  <Th className="hidden lg:table-cell">Last analysed</Th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((competitor) => (
                  <tr key={competitor.id} className="group transition-colors hover:bg-surface-raised">
                    <Td>
                      <Link
                        href={`/${orgId}/competitors/${competitor.id}`}
                        className="flex items-center gap-2.5"
                      >
                        <Favicon url={competitor.favicon_url} name={competitor.name} />
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-ink group-hover:text-accent">
                            {competitor.name}
                          </span>
                          <span className="block truncate text-xs text-ink-subtle">
                            {competitor.domain}
                          </span>
                        </span>
                      </Link>
                    </Td>
                    <Td className="hidden sm:table-cell">
                      {competitor.category ? (
                        <Badge variant="outline">{competitor.category}</Badge>
                      ) : (
                        <span className="text-ink-subtle">—</span>
                      )}
                    </Td>
                    <Td className="tabular text-right font-semibold">
                      {formatScore(competitor.latest_overall_score)}
                    </Td>
                    <Td className="hidden md:table-cell">
                      <ThreatBadge
                        level={
                          competitor.latest_overall_score === null
                            ? "unknown"
                            : competitor.latest_overall_score >= 80
                              ? "critical"
                              : competitor.latest_overall_score >= 60
                                ? "high"
                                : competitor.latest_overall_score >= 40
                                  ? "moderate"
                                  : "low"
                        }
                      />
                    </Td>
                    <Td className="hidden text-ink-muted lg:table-cell">
                      {humanize(competitor.importance)}
                    </Td>
                    <Td className="hidden text-ink-muted lg:table-cell">
                      {formatRelative(competitor.last_analyzed_at)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>

            {data.meta.total > PAGE_SIZE ? (
              <CardBody className="flex items-center justify-between border-t border-border">
                <p className="text-sm text-ink-muted">
                  {offset + 1}–{offset + data.items.length} of {data.meta.total}
                </p>
                <div className="flex gap-2">
                  {currentPage > 1 ? (
                    <Link
                      href={withQuery(`/${orgId}/competitors`, {
                        q,
                        status,
                        page: currentPage - 1,
                      })}
                      className="rounded-[--radius-control] border border-border px-3 py-1.5 text-sm text-ink hover:bg-surface-raised"
                    >
                      Previous
                    </Link>
                  ) : null}
                  {data.meta.has_more ? (
                    <Link
                      href={withQuery(`/${orgId}/competitors`, {
                        q,
                        status,
                        page: currentPage + 1,
                      })}
                      className="rounded-[--radius-control] border border-border px-3 py-1.5 text-sm text-ink hover:bg-surface-raised"
                    >
                      Next
                    </Link>
                  ) : null}
                </div>
              </CardBody>
            ) : null}
          </>
        )}
      </Card>
    </div>
  );
}
