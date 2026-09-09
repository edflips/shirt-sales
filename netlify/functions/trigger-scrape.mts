import type { Config } from "@netlify/functions";

// Fires the Daily Scrape workflow on GitHub at a fixed time. GitHub's own
// cron scheduler is best-effort and, for this repo, skipped or delayed every
// scheduled slot it was given; Netlify's scheduler is reliable. The workflow
// keeps its GitHub cron as a fallback, guarded so it doesn't double-run.
//
// Needs GH_DISPATCH_TOKEN in the site's environment variables: a fine-grained
// GitHub token for the shirt-sales repo with "Actions: read and write".
const REPO = "edflips/shirt-sales";
const WORKFLOW = "scrape.yml";

export default async () => {
  const token = Netlify.env.get("GH_DISPATCH_TOKEN");
  if (!token) {
    console.error("GH_DISPATCH_TOKEN is not set; cannot dispatch the scrape");
    return new Response("GH_DISPATCH_TOKEN not set", { status: 500 });
  }

  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "shirt-sales-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });

  if (res.status === 204) {
    console.log("Dispatched the Daily Scrape workflow");
    return new Response("dispatched", { status: 200 });
  }
  const detail = await res.text();
  console.error(`GitHub refused the dispatch: ${res.status} ${detail}`);
  return new Response(`github said ${res.status}`, { status: 502 });
};

// 01:17 UTC daily (02:17 BST): most listings go up before midnight, the run
// takes about an hour, so the data is fresh well before morning.
export const config: Config = { schedule: "17 1 * * *" };
