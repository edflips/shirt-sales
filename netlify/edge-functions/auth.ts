import type { Config, Context } from "@netlify/edge-functions";

// HTTP Basic Auth in front of the whole site. Credentials come from the
// DASH_USER / DASH_PASS environment variables set on the Netlify site —
// never from the repo. Fails closed: if they're missing, nothing is served.
export default async (request: Request, context: Context) => {
  const user = Netlify.env.get("DASH_USER");
  const pass = Netlify.env.get("DASH_PASS");
  if (!user || !pass) {
    return new Response("Dashboard auth is not configured.", { status: 503 });
  }

  const header = request.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice(6));
    } catch {
      decoded = "";
    }
    if (constantTimeEqual(decoded, `${user}:${pass}`)) {
      return context.next();
    }
  }

  return new Response("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="shirt-sales", charset="UTF-8"',
      "Cache-Control": "no-store",
    },
  });
};

function constantTimeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  }
  return diff === 0;
}

export const config: Config = { path: "/*" };
