import { redirect } from "next/navigation";

import { ApiClientError, api, serverFetch } from "@/lib/api";
import type { Session } from "@/lib/types";

/**
 * Entry point.
 *
 * Resolves where the user belongs — their first organization, or the sign-in page — and
 * redirects. Doing this server-side avoids a flash of the wrong screen.
 */
export default async function RootPage() {
  let session: Session;
  try {
    session = await serverFetch<Session>(api.auth.me);
  } catch (error) {
    if (error instanceof ApiClientError && error.isUnauthenticated) redirect("/login");
    throw error;
  }

  const organization = session.organizations[0];
  if (!organization) redirect("/login");

  redirect(`/${organization.id}`);
}
