import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { ApiClientError, api, serverFetch } from "@/lib/api";
import type { Session } from "@/lib/types";

/**
 * Authenticated shell.
 *
 * Resolves the session server-side and verifies that the signed-in user is a member of
 * the organization in the URL. The API enforces this too — this is the redirect that
 * makes a wrong URL land somewhere sensible instead of on an error page.
 */
export default async function OrgLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = await params;

  let session: Session;
  try {
    session = await serverFetch<Session>(api.auth.me);
  } catch (error) {
    if (error instanceof ApiClientError && error.isUnauthenticated) redirect("/login");
    throw error;
  }

  const membership = session.organizations.find((organization) => organization.id === orgId);
  if (!membership) {
    const fallback = session.organizations[0];
    redirect(fallback ? `/${fallback.id}` : "/login");
  }

  // A failure here must not take the whole app down — the badge is not load-bearing.
  let unreadCount = 0;
  try {
    const result = await serverFetch<{ count: number }>(api.org(orgId).unreadCount);
    unreadCount = result.count;
  } catch {
    unreadCount = 0;
  }

  return (
    <AppShell
      orgId={orgId}
      user={session.user}
      organizations={session.organizations}
      unreadCount={unreadCount}
    >
      {children}
    </AppShell>
  );
}
