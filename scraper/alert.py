"""Turn a run's status file into a GitHub Issue when Vinted blocked us.

Run by the last step of the scrape workflow (with `if: always()`), so it
sees the status file whether or not the scraper step succeeded. If the run
flagged a block it opens an issue labelled `scrape-blocked` assigned to the
repo owner — or comments on the one that's already open — and exits 1 so
the workflow run itself is marked failed. Two notification channels, one
of which (the issue) is impossible to miss.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

STATUS_PATH = Path("logs/run-status.json")
LABEL = "scrape-blocked"


def gh(*args: str, dry_run: bool = False) -> str:
    cmd = ["gh", *args]
    if dry_run:
        print("[dry-run]", " ".join(cmd))
        return ""
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def summary_markdown(status: dict) -> str:
    rows = [
        ("Active listings re-checked", status.get("active_checked")),
        ("Newly sold / gone", status.get("sold")),
        ("Unknown (left active)", status.get("unknown")),
        ("Blocked (403/429)", status.get("blocked")),
        ("Skipped (breaker tripped)", status.get("skipped")),
        ("Block signals in total", status.get("block_signals")),
        ("Listings found by search", status.get("found")),
        ("New listings added", status.get("new")),
    ]
    table = "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
    if status.get("alert"):
        return f"### :rotating_light: Vinted appears to be blocking the scraper\n\n**{status['alert_reason']}**\n\n{table}\n"
    return f"### Scrape run OK\n\n{table}\n"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    if not STATUS_PATH.exists():
        print(f"{STATUS_PATH} not found — scraper didn't get far enough to write it")
        return 0

    status = json.loads(STATUS_PATH.read_text())
    md = summary_markdown(status)
    print(md)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary and not dry_run:
        Path(step_summary).open("a").write(md)

    if not status.get("alert"):
        return 0

    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    run_url = ""
    if os.environ.get("GITHUB_SERVER_URL") and os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID"):
        run_url = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    date = (status.get("finished_at") or "")[:10]
    body = f"{md}\n\nRun: {run_url or '(local run)'}\n\nNothing was recorded as sold from the refused/unknown re-checks; they stay active and will be re-checked next run. If this persists, Vinted has probably started blocking the guest session — see `docs/03-scraping-strategy.md`."

    gh("label", "create", LABEL, "--color", "D93F0B", "--force",
       "--description", "The daily scrape looks blocked by Vinted", dry_run=dry_run)
    existing = gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number", "--jq", ".[0].number",
                  dry_run=dry_run)
    if existing:
        gh("issue", "comment", existing, "--body", f"Blocked again on {date}.\n\n{body}", dry_run=dry_run)
        print(f"Commented on open issue #{existing}")
    else:
        args = ["issue", "create", "--title", f"Vinted scrape blocked ({date})", "--label", LABEL, "--body", body]
        if owner:
            args += ["--assignee", owner]
        url = gh(*args, dry_run=dry_run)
        print(f"Opened issue {url}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
