import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ProvenanceBadge, ScoreBreakdown, SeverityBadge } from "@/components/intelligence";
import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { ApiClientError, api, serverFetch } from "@/lib/api";
import type { Report, ReportSection } from "@/lib/types";
import { formatDate, formatDateTime, humanize } from "@/lib/utils";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ orgId: string; reportId: string }>;
}): Promise<Metadata> {
  const { orgId, reportId } = await params;
  try {
    const report = await serverFetch<Report>(api.org(orgId).report(reportId));
    return { title: report.title };
  } catch {
    return { title: "Report" };
  }
}

export default async function ReportPage({
  params,
}: {
  params: Promise<{ orgId: string; reportId: string }>;
}) {
  const { orgId, reportId } = await params;

  let report: Report;
  try {
    report = await serverFetch<Report>(api.org(orgId).report(reportId));
  } catch (error) {
    if (error instanceof ApiClientError && error.isNotFound) notFound();
    throw error;
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <Link href={`/${orgId}/reports`} className="text-sm text-ink-muted hover:text-ink">
        ← All reports
      </Link>

      <PageHeader
        title={report.title}
        description={
          report.period_start && report.period_end
            ? `${formatDate(report.period_start)} – ${formatDate(report.period_end)} · generated ${formatDateTime(report.generated_at)}`
            : undefined
        }
      />

      {report.content?.is_mock ? (
        <Callout tone="warning" title="Contains development-provider output">
          Some analyses in this report were produced without an AI provider configured.
        </Callout>
      ) : null}

      {report.content ? (
        <div className="space-y-6">
          {report.content.sections.map((section, index) => (
            <SectionRenderer key={`${section.kind}-${index}`} section={section} />
          ))}
        </div>
      ) : (
        <Card>
          <CardBody>
            <p className="text-sm text-ink-muted">This report has no content.</p>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

/**
 * Renders one typed section.
 *
 * The report document is data, not markup, so the renderer decides presentation. Adding
 * a section kind means extending this switch — the stored documents stay valid.
 */
function SectionRenderer({ section }: { section: ReportSection }) {
  const provenance =
    "provenance" in section && section.provenance ? (
      <ProvenanceBadge source={section.provenance} />
    ) : null;

  switch (section.kind) {
    case "metrics":
      return (
        <Card>
          <CardHeader title={section.title} />
          <CardBody>
            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {section.metrics.map((metric) => (
                <div key={metric.label}>
                  <dt className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
                    {metric.label}
                  </dt>
                  <dd className="tabular mt-1 text-2xl font-semibold text-ink">
                    {metric.value === null || metric.value === undefined ? (
                      <span className="text-base font-normal text-ink-subtle">—</span>
                    ) : (
                      <>
                        {metric.value}
                        {metric.suffix ? (
                          <span className="text-base font-normal text-ink-subtle">
                            {metric.suffix}
                          </span>
                        ) : null}
                      </>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </CardBody>
        </Card>
      );

    case "text":
      return (
        <Card>
          <CardHeader title={section.title} action={provenance} />
          <CardBody>
            <p className="text-sm leading-relaxed text-ink-muted">{section.body}</p>
          </CardBody>
        </Card>
      );

    case "list":
      return (
        <Card>
          <CardHeader title={section.title} action={provenance} />
          <CardBody>
            <ul className="space-y-3">
              {section.items.map((item, index) => (
                <li key={`${item.title}-${index}`}>
                  {item.title ? (
                    <p className="text-sm font-medium text-ink">{item.title}</p>
                  ) : null}
                  {item.detail ? (
                    <p className="mt-0.5 text-sm text-ink-muted">{item.detail}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      );

    case "table":
      return (
        <Card>
          <CardHeader title={section.title} action={provenance} />
          <CardBody className="p-0">
            <Table caption={section.title}>
              <thead>
                <tr>
                  {section.columns.map((column, index) => (
                    <Th key={column} className={index === 0 ? undefined : "text-right"}>
                      {column}
                    </Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {section.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <Td
                        key={cellIndex}
                        className={
                          cellIndex === 0 ? "font-medium" : "tabular text-right"
                        }
                      >
                        {cell === null || cell === undefined ? (
                          <span className="text-ink-subtle">—</span>
                        ) : (
                          String(cell)
                        )}
                      </Td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </Table>
          </CardBody>
        </Card>
      );

    case "timeline":
      return (
        <Card>
          <CardHeader title={section.title} action={provenance} />
          <CardBody className="p-0">
            <ul className="divide-y divide-border">
              {section.events.map((event, index) => (
                <li key={`${event.at}-${index}`} className="px-5 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm text-ink">{event.title}</p>
                      {event.detail ? (
                        <p className="mt-0.5 text-sm text-ink-muted">{event.detail}</p>
                      ) : null}
                      <p className="mt-1 text-xs text-ink-subtle">
                        {event.competitor ? `${event.competitor} · ` : ""}
                        {event.type ? `${humanize(event.type)} · ` : ""}
                        <time dateTime={event.at}>{formatDateTime(event.at)}</time>
                      </p>
                    </div>
                    {event.severity ? <SeverityBadge severity={event.severity} /> : null}
                  </div>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      );

    case "score_matrix":
      return (
        <Card>
          <CardHeader title={section.title} action={provenance} />
          <CardBody>
            <ScoreBreakdown
              dimensions={section.dimensions}
              confidence={section.confidence}
              methodologyVersion={section.methodology_version}
            />
          </CardBody>
        </Card>
      );

    default:
      return null;
  }
}
