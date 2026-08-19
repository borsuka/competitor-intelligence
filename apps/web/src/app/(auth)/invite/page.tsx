import type { Metadata } from "next";

import { AcceptInvitationForm } from "@/components/account-forms";
import { ApiClientError, api, serverFetch } from "@/lib/api";
import type { Session } from "@/lib/types";

export const metadata: Metadata = {
  title: "Join a workspace",
  robots: { index: false, follow: false },
};

export default async function InvitePage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;

  // Resolved server-side so the page renders the right branch on first paint rather than
  // flashing "sign in" at someone who already is.
  let isSignedIn = false;
  try {
    await serverFetch<Session>(api.auth.me);
    isSignedIn = true;
  } catch (error) {
    if (!(error instanceof ApiClientError && error.isUnauthenticated)) throw error;
  }

  return <AcceptInvitationForm token={token ?? ""} isSignedIn={isSignedIn} />;
}
