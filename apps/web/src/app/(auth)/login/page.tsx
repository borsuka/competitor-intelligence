import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth-forms";
import { Skeleton } from "@/components/ui/primitives";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    // The form reads ?next= to resume an interrupted flow (an invitation link, say).
    // useSearchParams opts a component out of static prerendering unless it sits behind
    // a Suspense boundary, so the shell stays static and only the form is deferred.
    <Suspense fallback={<AuthFormSkeleton />}>
      <LoginForm />
    </Suspense>
  );
}

function AuthFormSkeleton() {
  return (
    <div className="space-y-6" aria-hidden>
      <div className="space-y-2">
        <Skeleton className="h-7 w-28" />
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    </div>
  );
}
