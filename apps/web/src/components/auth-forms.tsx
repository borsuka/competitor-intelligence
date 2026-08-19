"use client";

/**
 * Sign-in and sign-up forms.
 *
 * Client components because they hold form state. Validation runs through Zod schemas
 * that mirror the API's own rules, so the user is told about a short password before a
 * round trip rather than after one — but the API validates independently, since client
 * validation is a convenience and never a control.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, Callout, Field, Input } from "@/components/ui/primitives";
import { ApiClientError, api, clientFetch } from "@/lib/api";
import type { Session } from "@/lib/types";

const MIN_PASSWORD = 12;

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

const registerSchema = z.object({
  full_name: z.string().min(1, "Enter your name.").max(120),
  email: z.string().email("Enter a valid email address."),
  password: z
    .string()
    .min(MIN_PASSWORD, `Use at least ${MIN_PASSWORD} characters. A passphrase works well.`)
    .max(200),
  organization_name: z.string().max(120).optional(),
});

type LoginValues = z.infer<typeof loginSchema>;
type RegisterValues = z.infer<typeof registerSchema>;

function useAuthSubmit() {
  const router = useRouter();
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(path: string, body: unknown) {
    setFormError(null);
    try {
      const session = await clientFetch<Session>(path, { method: "POST", body });
      const organization = session.organizations[0];
      // Refresh so the server components re-run with the new session cookie.
      router.replace(organization ? `/${organization.id}` : "/");
      router.refresh();
    } catch (error) {
      if (error instanceof ApiClientError) {
        setFormError(error.displayMessage);
      } else {
        setFormError("Could not reach the server. Check your connection and try again.");
      }
    }
  }

  return { submit, formError };
}

export function LoginForm() {
  const { submit, formError } = useAuthSubmit();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Sign in</h1>
      <p className="mt-1 text-sm text-ink-muted">
        Welcome back. Enter your details to continue.
      </p>

      <form
        className="mt-6 space-y-4"
        onSubmit={handleSubmit((values) => submit(api.auth.login, values))}
        noValidate
      >
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

        <Field label="Password" htmlFor="password" error={errors.password?.message}>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            aria-invalid={Boolean(errors.password)}
            {...register("password")}
          />
        </Field>

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? <Loader2 className="animate-spin" aria-hidden /> : null}
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      <p className="mt-6 text-sm text-ink-muted">
        No account yet?{" "}
        <Link href="/register" className="font-medium text-accent hover:underline">
          Create one
        </Link>
      </p>
    </div>
  );
}

export function RegisterForm() {
  const { submit, formError } = useAuthSubmit();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema) });

  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Create your workspace</h1>
      <p className="mt-1 text-sm text-ink-muted">
        You will be the owner. You can invite your team afterwards.
      </p>

      <form
        className="mt-6 space-y-4"
        onSubmit={handleSubmit((values) => submit(api.auth.register, values))}
        noValidate
      >
        {formError ? <Callout tone="danger">{formError}</Callout> : null}

        <Field label="Your name" htmlFor="full_name" error={errors.full_name?.message}>
          <Input
            id="full_name"
            autoComplete="name"
            autoFocus
            aria-invalid={Boolean(errors.full_name)}
            {...register("full_name")}
          />
        </Field>

        <Field label="Work email" htmlFor="email" error={errors.email?.message}>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            aria-invalid={Boolean(errors.email)}
            {...register("email")}
          />
        </Field>

        <Field
          label="Password"
          htmlFor="password"
          hint={`At least ${MIN_PASSWORD} characters. Length beats symbols — a passphrase is stronger and easier to remember.`}
          error={errors.password?.message}
        >
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            aria-invalid={Boolean(errors.password)}
            {...register("password")}
          />
        </Field>

        <Field
          label="Company or team name"
          htmlFor="organization_name"
          hint="Optional. You can rename it later."
          error={errors.organization_name?.message}
        >
          <Input id="organization_name" autoComplete="organization" {...register("organization_name")} />
        </Field>

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? <Loader2 className="animate-spin" aria-hidden /> : null}
          {isSubmitting ? "Creating…" : "Create workspace"}
        </Button>
      </form>

      <p className="mt-6 text-sm text-ink-muted">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
