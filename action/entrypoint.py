#!/usr/bin/env python3
"""GitHub Action entrypoint for statewatch.

Runs `statewatch scan --output json`, renders a Markdown summary, upserts it as a single
PR comment (find-and-replace by marker, never spam), and maps the scan exit code to the
check conclusion: exit 2 -> action fails the check; 0/1 -> passes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

MARKER = "<!-- statewatch-action -->"


def _in(name: str, default: str = "") -> str:
    return os.environ.get(f"INPUT_{name.upper().replace('-', '_')}", default).strip()


def _run_scan() -> tuple[int, dict]:
    cmd = [
        "statewatch", "scan",
        "--tfstate", _in("tfstate"),
        "--project", _in("project"),
        "--output", "json",
    ]
    if _in("config"):
        cmd += ["--config", _in("config")]
    if _in("stub") in ("1", "true", "yes"):
        cmd += ["--stub"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1, 2) or not proc.stdout.strip():
        sys.stderr.write(proc.stderr or "statewatch scan produced no output\n")
        sys.exit(1)
    return proc.returncode, json.loads(proc.stdout)


def _markdown(code: int, report: dict) -> str:
    s = report["summary"]
    head = "✅ No drift detected." if not report["findings"] else (
        f"**{s['findings']} finding(s)** · highest **{s['highest_severity']}** · exit {code}"
    )
    lines = [MARKER, "## statewatch", "", head, ""]
    for f in report["findings"]:
        labels: dict[str, int] = {}
        for n in f["impacted"]:
            labels[n["label"]] = labels.get(n["label"], 0) + 1
        impact = ", ".join(f"{v} {k}" for k, v in sorted(labels.items())) or "no downstream impact"
        lines.append(
            f"- **{f['severity']}** `{f['resource_type']}` **{f['name']}** "
            f"({f['status']}) — {impact}"
        )
    lines += ["", "_Severity is a heuristic proxy for propagation, not dataflow analysis._"]
    return "\n".join(lines)


def _gh(method: str, url: str, token: str, body: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (github api)
        return json.loads(resp.read() or "null")


def _upsert_comment(markdown: str) -> None:
    token = _in("github-token") or os.environ.get("GITHUB_TOKEN", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not (token and event_path and repo):
        return
    with open(event_path) as fh:
        event = json.load(fh)
    pr = (event.get("pull_request") or {}).get("number") or event.get("number")
    if not pr:
        return  # not a PR context — comment step is skipped silently
    base = f"https://api.github.com/repos/{repo}"
    try:
        comments = _gh("GET", f"{base}/issues/{pr}/comments?per_page=100", token)
        existing = next(
            (c for c in comments if MARKER in (c.get("body") or "")), None  # type: ignore[union-attr]
        )
        if existing:
            _gh("PATCH", f"{base}/issues/comments/{existing['id']}", token, {"body": markdown})
        else:
            _gh("POST", f"{base}/issues/{pr}/comments", token, {"body": markdown})
    except urllib.error.URLError as exc:
        sys.stderr.write(f"statewatch: could not post PR comment: {exc}\n")


def main() -> None:
    code, report = _run_scan()
    markdown = _markdown(code, report)
    print(markdown)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as fh:
            fh.write(markdown + "\n")

    if _in("post-comment", "true") in ("1", "true", "yes"):
        _upsert_comment(markdown)

    # Check conclusion: exit 2 (critical / significant blast radius) fails the check.
    sys.exit(1 if code == 2 else 0)


if __name__ == "__main__":
    main()
