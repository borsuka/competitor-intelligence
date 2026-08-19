"use client";

import { TriangleAlert } from "lucide-react";
import { useEffect } from "react";

import { Button, Card, EmptyState } from "@/components/ui/primitives";

/**
 * Route-level error boundary.
 *
 * Says what happened in plain language and offers the one action that usually helps. The
 * digest is shown because it is what correlates this screen with a server log entry.
 */
export default function OrgError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Logged rather than swallowed. A real deployment forwards this to Sentry, which is
    // configured behind SENTRY_DSN.
    console.error(error);
  }, [error]);

  return (
    <Card>
      <EmptyState
        icon={<TriangleAlert className="size-5" aria-hidden />}
        title="This page could not be loaded"
        description="The request failed on its way to the API. This is usually temporary."
        action={
          <div className="flex flex-col items-center gap-2">
            <Button onClick={reset}>Try again</Button>
            {error.digest ? (
              <p className="font-mono text-xs text-ink-subtle">Reference: {error.digest}</p>
            ) : null}
          </div>
        }
      />
    </Card>
  );
}
