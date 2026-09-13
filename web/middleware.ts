/**
 * HTTP Basic authentication over the whole app.
 *
 * This tool is deployed fully private: there is no public read surface, and the
 * API has no public address at all — it is reachable only over Fly's private
 * network, from this Next.js server. So a single check here is the entire
 * authentication story, which is why it guards the proxy route too.
 *
 * Basic auth rather than a bearer token because the front end is a browser app.
 * The browser handles the credential prompt and re-sends the header on every
 * request, including from client-side `fetch` to the same origin, with no login
 * page to build and no session to store. Over TLS (which Fly terminates) the
 * credentials are encrypted in transit.
 *
 * If `APP_USERNAME` / `APP_PASSWORD` are unset the app runs open. That is the
 * right default for `npm run dev` on localhost, and `/api/health` reports which
 * mode is in force so a deployment cannot be accidentally public without it
 * being visible.
 */

import { NextRequest, NextResponse } from "next/server";

export const config = {
  // Everything except Next's own static output and /healthz. The proxy route is
  // deliberately included: it is the only path to the API.
  //
  // /healthz is exempt because Fly's health checker cannot present credentials,
  // and weakening this middleware to let the checker through would be a far
  // worse trade than exposing a route that returns a fixed string.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|healthz).*)"],
};

/**
 * Constant-time string comparison.
 *
 * A plain `===` on secrets leaks their length and prefix through timing. The
 * practical risk over the internet is small, but the correct version is four
 * lines and there is no reason to ship the sloppy one.
 */
function safeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  // Compare a fixed-length digest of each side so differing lengths do not
  // short-circuit the loop.
  let mismatch = left.length ^ right.length;
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    mismatch |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return mismatch === 0;
}

function unauthorized(): NextResponse {
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Options Analytics", charset="UTF-8"',
      "Cache-Control": "no-store",
    },
  });
}

export function middleware(request: NextRequest) {
  const username = process.env.APP_USERNAME;
  const password = process.env.APP_PASSWORD;

  // No credentials configured: run open. Intended for local development.
  if (!username || !password) return NextResponse.next();

  const header = request.headers.get("authorization");
  if (!header?.startsWith("Basic ")) return unauthorized();

  let decoded: string;
  try {
    decoded = atob(header.slice("Basic ".length));
  } catch {
    return unauthorized();
  }

  // Split on the first colon only — a password may legitimately contain one.
  const separator = decoded.indexOf(":");
  if (separator < 0) return unauthorized();

  const suppliedUser = decoded.slice(0, separator);
  const suppliedPassword = decoded.slice(separator + 1);

  // Both comparisons always run; `&&` would skip the second on a bad username
  // and reintroduce the timing signal safeEqual exists to remove.
  const userOk = safeEqual(suppliedUser, username);
  const passwordOk = safeEqual(suppliedPassword, password);

  return userOk && passwordOk ? NextResponse.next() : unauthorized();
}
