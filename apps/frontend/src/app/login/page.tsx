"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";
import { useAuth } from "@/hooks/useAuth";

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code})` : "";
    return `Login failed [${error.status}${code}]: ${error.message}`;
  }
  if (error instanceof Error) {
    return `Login failed: ${error.message}`;
  }
  return "Login failed.";
}

export default function LoginPage() {
  const router = useRouter();
  const { login, isLoading } = useAuth();
  const savedScope = getStoredScope();
  const [tenantId, setTenantId] = useState(savedScope?.tenantId ?? "");
  const [workspace, setWorkspace] = useState(savedScope?.workspace ?? "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    setIsSubmitting(true);
    try {
      await login({
        tenantId,
        workspace,
        email,
        password,
      });
      router.push("/findings");
    } catch (error) {
      setSubmitError(loginErrorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md items-center px-6">
      <form className="w-full space-y-4 rounded border border-zinc-200 p-6" onSubmit={handleSubmit}>
        <div>
          <h1 className="text-2xl font-semibold">Sign in</h1>
          <p className="text-sm text-zinc-600">Use your tenant/workspace credentials.</p>
        </div>
        <label className="block text-sm">
          <span className="mb-1 block">Tenant ID</span>
          <input
            className="w-full rounded border border-zinc-300 px-3 py-2"
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
            required
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block">Workspace</span>
          <input
            className="w-full rounded border border-zinc-300 px-3 py-2"
            value={workspace}
            onChange={(event) => setWorkspace(event.target.value)}
            required
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block">Email</span>
          <input
            className="w-full rounded border border-zinc-300 px-3 py-2"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block">Password</span>
          <input
            className="w-full rounded border border-zinc-300 px-3 py-2"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {submitError ? <p className="text-sm text-red-600">{submitError}</p> : null}
        <button
          type="submit"
          className="w-full rounded bg-zinc-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          disabled={isSubmitting || isLoading}
        >
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
