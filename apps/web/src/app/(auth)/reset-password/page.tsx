import type { Metadata } from "next";

import { ResetPasswordForm } from "@/components/account-forms";

export const metadata: Metadata = {
  title: "Choose a new password",
  // The token is in the URL. Even though the page is already noindex globally, this is
  // the one place where a leaked URL is a live credential.
  robots: { index: false, follow: false },
};

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token } = await searchParams;
  return <ResetPasswordForm token={token ?? ""} />;
}
