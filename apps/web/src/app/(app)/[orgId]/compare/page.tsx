import type { Metadata } from "next";

import { ComparisonBuilder } from "@/components/comparison";
import { PageHeader } from "@/components/ui/primitives";
import { api, serverFetch, withQuery } from "@/lib/api";
import type { Competitor, Paginated } from "@/lib/types";

export const metadata: Metadata = { title: "Compare" };

export default async function ComparePage({
  params,
}: {
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;

  const competitors = await serverFetch<Paginated<Competitor>>(
    withQuery(api.org(orgId).competitors, { status: "active", limit: 100, sort: "score" }),
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compare"
        description="Put competitors side by side. Scores come from the deterministic engine; the narrative is AI-generated and labelled as such."
      />
      <ComparisonBuilder orgId={orgId} competitors={competitors.items} />
    </div>
  );
}
