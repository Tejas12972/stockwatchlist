/**
 * Platform health check for the web app.
 *
 * Excluded from the auth middleware on purpose. Fly's health checker cannot
 * present credentials, and the alternative — weakening the middleware so the
 * checker gets through — would be a far worse trade than exposing a route that
 * returns a fixed string and reads nothing.
 *
 * It deliberately does **not** check the upstream API. A readiness probe that
 * fails because the *API* is down would have Fly restart the *web* machine,
 * which cannot fix it, turning one degraded service into two.
 */

export const dynamic = "force-dynamic";

export function GET() {
  return new Response("ok", {
    status: 200,
    headers: { "content-type": "text/plain", "cache-control": "no-store" },
  });
}
