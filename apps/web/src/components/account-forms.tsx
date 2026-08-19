"use client";

/**
 * Account recovery and invitation acceptance.
 *
 * The two flows a user hits when something has gone wrong or when they are joining
 * someone else's workspace — the paths that matter most when they are missing, because
 * without them an account is unrecoverable and a team is unjoinable.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Loader2, MailCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Callout, Field, Input } from "@/components/ui/primitives";
import { ApiClientError, api, clientFetch } from "@/lib/api";

const MIN_PASSWORD = 12;

/* ------------------------------------------------------------ forgot password */

const requestSchema = z.object({
  email: z.string().email("Enter a valid email address."),
});

export function ForgotPasswordForm() {
  const [sent, setSent] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof requestSchema>>({ resolver: zodResolver(requestSchema) });

  async function onSubmit(values: z.infer<typeof requestSchema>) {
    setFormError(null);
    try {
      await clientFetch(api.auth.passwordReset, { method: "POST", body: values });
      setSent(true);
    } catch (error) {
      setFormError(
        error instanceof ApiClientError
          ? error.displayMessage
          : "Could not reach the server. Try again.",
      );
    }
  }

  if (sent) {
    // The API answers identically whether or not the address exists, so this screen must
    // too. Saying "we sent it" only for real accounts would be a user enumeration oracle
    // built in the UI after the API carefully avoided one.
    return (
      <div>
        <div className="mb-4 flex size-10 items-center justify-center rounded-full bg-accent-soft text-accent">
          <MailCheck className="size-5" aria-hidden />
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Check your email</h1>
        <p className="mt-2 text-sm text-ink-muted">
          If an account exists for <strong className="text-ink">{getValues("email")}</strong>,
          a reset link is on its way. The link is valid for one hour and can be used once.
        </p>
        <Callout tone="neutral" title="No email transport is configured">
          This deployment does not send mail yet. In development the reset token is written
          to the API log — look for <code className="font-mono text-xs">
          auth.password_reset_token_issued</code>.
        </Callout>
        <p className="mt-6 text-sm text-ink-muted">
          <Link href="/login" className="font-medium text-accent hover:underline">
            Back to sign in
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Reset your password</h1>
      <p className="mt-1 text-sm text-ink-muted">
        Enter your email address and we will send you a link.
      </p>

      <form className="mt-6 space-y-4" onSubmit={handleSubmit(onSubmit)} noValidate>
        {formError ? <Callout tone="danger">{formError}</Callout> : null}

        <Field label="Email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            autoFocus
            aria-invalid={Boolean(errors.email)}
            {...register("email")}
          />
        </Field>

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? <Loader2 className="animate-spin" aria-hidden /> : null}
          {isSubmitting ? "Sending…" : "Send reset link"}
        </Button>
      </form>

      <p className="mt-6 text-sm text-ink-muted">
        Remembered it?{" "}
        <Link href="/login" className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}

/* ------------------------------------------------------------- reset password */

const resetSchema = z
  .object({
    new_password: z
      .string()
      .min(MIN_PASSWORD, `Use at least ${MIN_PASSWORD} characters.`)
      .max(200),
    confirm: z.string(),
  })
  .refine((values) => values.new_password === values.confirm, {
    message: "The two passwords do not match.",
    path: ["confirm"],
  });

export function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
  const [done, setDone] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof resetSchema>>({ resolver: zodResolver(resetSchema) });

  async function onSubmit(values: z.infer<typeof resetSchema>) {
    setFormError(null);
    try {
      await clientFetch(api.auth.passwordResetConfirm, {
        method: "POST",
        body: { token, new_password: values.new_password },
      });
      setDone(true);
    } catch (error) {
      setFormError(
        error instanceof ApiClientError
          ? error.displayMessage
          : "Could not reach the server. Try again.",
      );
    }
  }

  if (!token) {
    return (
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Link is incomplete</h1>
        <p className="mt-2 text-sm text-ink-muted">
          This reset link is missing its token. Request a new one.
        </p>
        <Link href="/forgot-password">
          <Button className="mt-6 w-full">Request a new link</Button>
        </Link>
      </div>
    );
  }

  if (done) {
    return (
      <div>
        <div className="mb-4 flex size-10 items-center justify-center rounded-full bg-low-soft text-low">
          <CheckCircle2 className="size-5" aria-hidden />
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Password changed</h1>
        <p className="mt-2 text-sm text-ink-muted">
          Every other session has been signed out, which is the right thing to happen if
          someone else had access.
        </p>
        <Button className="mt-6 w-full" onClick={() => router.push("/login")}>
          Sign in
        </Button>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Choose a new password</h1>
      <p className="mt-1 text-sm text-ink-muted">
        This will sign out every other device.
      </p>

      <form className="mt-6 space-y-4" onSubmit={handleSubmit(onSubmit)} noValidate>
        {formError ? <Callout tone="danger">{formError}</Callout> : null}

        <Field
          label="New password"
          htmlFor="new_password"
          hint={`At least ${MIN_PASSWORD} characters. A passphrase is stronger and easier to remember.`}
          error={errors.new_password?.message}
        >
          <Input
            id="new_password"
            type="password"
            autoComplete="new-password"
            autoFocus
            aria-invalid={Boolean(errors.new_password)}
            {...register("new_password")}
          />
        </Field>

        <Field label="Confirm password" htmlFor="confirm" error={errors.confirm?.message}>
          <Input
            id="confirm"
            type="password"
            autoComplete="new-password"
            aria-invalid={Boolean(errors.confirm)}
            {...register("confirm")}
          />
        </Field>

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? <Loader2 className="animate-spin" aria-hidden /> : null}
          {isSubmitting ? "Saving…" : "Change password"}
        </Button>
      </form>
    </div>
  );
}

/* --------------------------------------------------------------- invitations */

export function AcceptInvitationForm({
  token,
  isSignedIn,
}: {
  token: string;
  isSignedIn: boolean;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      await clientFetch(api.invitations.accept, { method: "POST", body: { token } });
      router.replace("/");
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiClientError
          ? caught.displayMessage
          : "Could not accept the invitation.",
      );
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Invitation incomplete</h1>
        <p className="mt-2 text-sm text-ink-muted">
          This invitation link is missing its token. Ask whoever invited you to send it
          again.
        </p>
      </div>
    );
  }

  if (!isSignedIn) {
    // The invitation is addressed to an email, so it can only be redeemed by the account
    // that owns it. Sending them to sign in first is the honest order of operations.
    const next = encodeURIComponent(`/invite?token=${token}`);
    return (
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          You have been invited
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          Sign in with the address the invitation was sent to, or create an account with it,
          and the invitation will be waiting.
        </p>
        <div className="mt-6 space-y-2">
          <Link href={`/login?next=${next}`} className="block">
            <Button className="w-full">Sign in</Button>
          </Link>
          <Link href={`/register?next=${next}`} className="block">
            <Button variant="secondary" className="w-full">
              Create an account
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Join the workspace</h1>
      <p className="mt-1 text-sm text-ink-muted">
        Accepting adds your account to the workspace you were invited to.
      </p>

      {error ? (
        <div className="mt-4">
          <Callout tone="danger">{error}</Callout>
        </div>
      ) : null}

      <div className="mt-6 space-y-2">
        <Button className="w-full" onClick={accept} disabled={busy}>
          {busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
          {busy ? "Joining…" : "Accept invitation"}
        </Button>
        <Link href="/" className="block">
          <Button variant="ghost" className="w-full">
            Not now
          </Button>
        </Link>
      </div>
    </div>
  );
}
