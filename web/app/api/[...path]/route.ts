/**
 * Server-side proxy to the analytics API.
 *
 * The browser never talks to the API directly. It calls this same-origin route,
 * which forwards to the API over Fly's private network. Four things fall out of
 * that:
 *
 * 1. **The API needs no public address.** It is unreachable from the internet,
 *    so the only thing that has to be authenticated is this Next.js app.
 * 2. **CORS stops existing.** Same origin, no preflight, no allow-list to keep
 *    in sync with whatever domain the app ends up on.
 * 3. **The API URL becomes runtime configuration.** The previous design baked
 *    `NEXT_PUBLIC_API_BASE_URL` into the client bundle at build time, so moving
 *    the API meant rebuilding the image. `API_INTERNAL_URL` is read per request.
 * 4. **One image runs anywhere** — local, staging, production — because nothing
 *    environment-specific is compiled in.
 */

import { NextRequest, NextResponse } from "next/server";

// Always hit the upstream; never serve a cached chain as if it were live.
export const dynamic = "force-dynamic";

const API_INTERNAL_URL =
  process.env.API_INTERNAL_URL ?? "http://localhost:8000";

// Longer than a browser's patience but shorter than a hung socket. A cold chain
// fetch from the vendor can legitimately take a while.
const UPSTREAM_TIMEOUT_MS = 30_000;

async function proxy(request: NextRequest, path: string[]): Promise<NextResponse> {
  const target = new URL(
    `${API_INTERNAL_URL.replace(/\/$/, "")}/${path.join("/")}`,
  );
  target.search = request.nextUrl.search;

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  // Deliberately not forwarded: the browser's Authorization header. It
  // authenticates the user to *this* app; the API sits on a private network and
  // has no notion of it.

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body:
        request.method === "GET" || request.method === "HEAD"
          ? undefined
          : await request.text(),
      signal: controller.signal,
      cache: "no-store",
    });

    const body = await upstream.text();
    return new NextResponse(body || null, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch (cause) {
    const timedOut = cause instanceof Error && cause.name === "AbortError";
    // Returned in the API's own error shape so the client has one contract to
    // handle rather than two.
    return NextResponse.json(
      {
        code: timedOut ? "upstream_timeout" : "api_unreachable",
        message: timedOut
          ? "The analytics API did not respond in time."
          : "Could not reach the analytics API.",
        detail: null,
      },
      { status: timedOut ? 504 : 502, headers: { "cache-control": "no-store" } },
    );
  } finally {
    clearTimeout(timeout);
  }
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
