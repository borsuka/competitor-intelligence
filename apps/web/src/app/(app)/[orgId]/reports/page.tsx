import { FileText } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { GenerateReport } from "@/components/forms";
import {
  Badge,
  Card,
  CardBody,
  EmptyState,
  PageHeader,
} from "@/components/ui/primitives";
import { api, serverFetch, withQuery } from "@/lib/api";
import type { Competitor, Paginated, Report } from "@/lib/types";
import { formatDate, formatRelative, humanize } from "@/lib/utils";

export const metadata: Metadata = { title: "Reports" };

export default async function ReportsPage({
  params,
}: {
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;

  const [reports, competitors] = await Promise.all([
    serverFetch<Report[]>(api.org(orgId).reports),
    serverFetch<Paginated<Competitor>>(
      withQuery(api.org(orgId).competitors, { status: "active", limit: 100 }),
    ),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reports"
        description="Point-in-time documents built from stored analyses. A report shows what was true when it was generated."
        actions={<GenerateReport orgId={orgId} competitors={competitors.items} />}
      />

      <Card>
        {reports.length === 0 ? (
          <EmptyState
            icon={<FileText className="size-5" aria-hidden />}
            title="No reports yet"
            description="Generate a weekly intelligence summary, a competitor overview, or a side-by-side comparison. Reports read from data you already have, so they are instant."
            action={<GenerateReport orgId={orgId} competitors={competitors.items} />}
          />
        ) : (
          <CardBody className="p-0">
            <ul className="divide-y divide-border">
              {reports.map((report) => (
                <li key={report.id}>
                  <Link
                    href={`/${orgId}/reports/${report.id}`}
                    className="flex items-center justify-between gap-4 px-5 py-4 transition-colors hover:bg-surface-raised"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-ink">{report.title}</p>
                      <p className="mt-0.5 text-xs text-ink-subtle">
                        {humanize(report.report_type)}
                        {report.period_start && report.period_end
                          ? ` · ${formatDate(report.period_start)} – ${formatDate(report.period_end)}`
                          : ""}
                        {" · "}
                        <time dateTime={report.created_at}>
                          {formatRelative(report.created_at)}
                        </time>
                      </p>
                    </div>
                    {report.status !== "completed" ? (
                      <Badge>{humanize(report.status)}</Badge>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          </CardBody>
        )}
      </Card>
    </div>
  );
}
