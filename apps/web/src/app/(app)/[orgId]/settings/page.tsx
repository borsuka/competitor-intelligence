import type { Metadata } from "next";

import { CompanyProfileForm, InviteMemberForm } from "@/components/forms";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  Progress,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { api, serverFetch } from "@/lib/api";
import type { Member, Organization, Usage } from "@/lib/types";
import { formatDate, formatNumber, humanize } from "@/lib/utils";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage({
  params,
}: {
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;

  const [organization, usage, members] = await Promise.all([
    serverFetch<Organization>(api.org(orgId).root),
    serverFetch<Usage>(api.org(orgId).usage),
    serverFetch<Member[]>(api.org(orgId).members),
  ]);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="Settings"
        description="Workspace, team and plan usage."
      />

      <Card>
        <CardHeader
          title="Workspace"
          description="Your own company profile is what makes recommendations specific rather than generic."
        />
        <CardBody>
          <CompanyProfileForm
            orgId={orgId}
            defaults={{
              name: organization.name,
              own_company_name: organization.own_company_name,
              own_company_url: organization.own_company_url,
              own_company_description: organization.own_company_description,
            }}
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Usage this month"
          description={`Period ${usage.period}. Limits reset at the start of each month.`}
          action={<Badge variant="accent">{humanize(organization.plan)} plan</Badge>}
        />
        <CardBody className="space-y-5">
          <UsageBar
            label="Competitors tracked"
            used={usage.competitors_used}
            limit={usage.competitors_limit}
          />
          <UsageBar
            label="Analyses"
            used={usage.analyses_used}
            limit={usage.analyses_limit}
          />
          <UsageBar
            label="Pages crawled"
            used={usage.pages_crawled}
            limit={usage.pages_limit}
          />
          <p className="text-xs text-ink-subtle">
            AI tokens this period: {formatNumber(usage.ai_tokens_in)} in,{" "}
            {formatNumber(usage.ai_tokens_out)} out. Analyses are skipped when a
            competitor&apos;s pages have not changed, so re-running one is often free.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Team" description={`${members.length} member${members.length === 1 ? "" : "s"}`} />
        <CardBody className="p-0">
          <Table caption="Workspace members">
            <thead>
              <tr>
                <Th>Member</Th>
                <Th>Role</Th>
                <Th className="hidden sm:table-cell">Joined</Th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.id}>
                  <Td>
                    <span className="block font-medium text-ink">{member.full_name}</span>
                    <span className="block text-xs text-ink-subtle">{member.email}</span>
                  </Td>
                  <Td>
                    <Badge variant={member.role === "owner" ? "accent" : "outline"}>
                      {humanize(member.role)}
                    </Badge>
                  </Td>
                  <Td className="hidden text-ink-muted sm:table-cell">
                    {formatDate(member.joined_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Invite a teammate"
          description="Admins manage competitors and alerts. Members run analyses. Viewers read only."
        />
        <CardBody>
          <InviteMemberForm orgId={orgId} />
        </CardBody>
      </Card>
    </div>
  );
}

function UsageBar({
  label,
  used,
  limit,
}: {
  label: string;
  used: number;
  limit: number;
}) {
  const percentage = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const nearLimit = percentage >= 80;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-ink">{label}</span>
        <span className={`tabular text-sm ${nearLimit ? "text-critical" : "text-ink-muted"}`}>
          {formatNumber(used)} / {formatNumber(limit)}
        </span>
      </div>
      <Progress value={percentage} label={`${label}: ${used} of ${limit} used`} className="mt-2" />
    </div>
  );
}
