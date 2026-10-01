/**
 * Cloudflare Pages Function — defense in depth for the /approve path.
 *
 * Cloudflare Access is the real gate: it intercepts the request at the edge
 * and never forwards an unauthenticated one. This function does not replace
 * it. It exists so that a misconfigured Access application cannot silently
 * expose order execution.
 *
 * How the identity header works
 * -----------------------------
 * After a successful Access check, Cloudflare ADDS these headers. It also
 * STRIPS any client-supplied header with the same name before the request
 * reaches the origin, so a value arriving here genuinely came from Cloudflare
 * and not from a browser.
 *
 *   Cf-Access-Authenticated-User-Email
 *   Cf-Access-Jwt-Assertion            (a signed JWT, if you enabled it)
 *
 * Never do this check in client-side JavaScript. Anything the browser can read,
 * an attacker can forge.
 *
 * Deploy: drop this into `functions/approve/_middleware.js`
 */

const ALLOWED = ["kenkenlui"];   // ← Cloudflare account: Kenkenlui@gmail.com

export async function onRequest(context) {
  const { request, env, next } = context;
  const email = request.headers.get("Cf-Access-Authenticated-User-Email") || "";
  const local = email.split("@")[0].toLowerCase();

  if (!email || !ALLOWED.includes(local)) {
    // 403, not 302: redirecting to login would loop if Access is misconfigured.
    return new Response("Access denied", {
      status: 403,
      headers: {
        "content-type": "text/plain",
        "cache-control": "no-store",
        "x-why": email ? "email not on allowlist" : "no access header — is Access configured?",
      },
    });
  }

  // Optionally re-verify the JWT rather than trusting the email header alone.
  // Uncomment once you have a team domain set up.
  //
  // const jwt = request.headers.get("Cf-Access-Jwt-Assertion");
  // const { verify } = await import("jose");
  // try {
  //   const { payload } = await jwtVerify(jwt, await jose.jwkFromKeyset(jwks));
  //   if (payload.email !== email) throw new Error("mismatch");
  // } catch { return new Response("bad token", { status: 403 }); }

  const res = await next();
  res.headers.set("cache-control", "private, no-store");
  res.headers.set("x-access-user", local);
  return res;
}
