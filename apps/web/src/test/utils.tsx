import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { vi } from "vitest";

export function renderWithQuery(ui: ReactElement, options?: RenderOptions) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(ui, { wrapper: Wrapper, ...options });
}

/** Stub `fetch` with a handler keyed by URL path. */
export function mockFetch(handler: (path: string) => { status?: number; body: unknown } | "pending") {
  const fn = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const result = handler(new URL(url, "http://localhost").pathname);
    if (result === "pending") return new Promise<Response>(() => {});
    return Promise.resolve(
      new Response(JSON.stringify(result.body), {
        status: result.status ?? 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
