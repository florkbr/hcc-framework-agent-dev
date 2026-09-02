#!/usr/bin/env python3
"""Check for merged PRs with failed checks (KEDA scheduled)."""

import subprocess
import json
import logging
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from common import (
    load_project_repos,
    upstream_repo,
    output_result,
    get_capacity,
    get_tasks,
)
from lib.config import load_config
from lib.security_scans import is_security_scan

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Timeout constants (seconds)
TIMEOUT_PR_VIEW = 10  # Individual PR check status
TIMEOUT_PR_LIST = 30  # List merged PRs
MAX_PRS_PER_REPO = 50  # Limit PRs fetched per repo
MAX_CONCURRENT_VIOLATIONS = 5  # Max violations to process at once

SCAN_INTERVAL_HOURS = 24  # Lookback window for PR merges


def load_scan_config() -> Optional[Dict[str, Any]]:
    """Load scan configuration with repo whitelist."""
    config_path = Path(__file__).parent.parent / "test-config.yaml"
    if not config_path.exists():
        logger.warning(f"Scan config not found at {config_path}")
        return None

    try:
        with open(config_path) as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse scan config: {e}")
        return None


def parse_merged_at(merged_at_str: Optional[str]) -> Optional[datetime]:
    """Safely parse GitHub mergedAt timestamp.

    Args:
        merged_at_str: ISO format timestamp from GitHub API

    Returns:
        Timezone-aware datetime or None if invalid
    """
    if not merged_at_str:
        return None
    try:
        return datetime.fromisoformat(merged_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError) as e:
        logger.warning(f"Invalid mergedAt format: {merged_at_str} - {e}")
        return None


def extract_pr_metadata(pr_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant PR metadata safely.

    Args:
        pr_data: PR data from GitHub API

    Returns:
        Dict with extracted metadata fields
    """
    return {
        "number": pr_data.get("number"),
        "title": pr_data.get("title", ""),
        "url": pr_data.get("url", ""),
        "author": pr_data.get("author", {}).get("login", "unknown"),
        "merged_at": pr_data.get("mergedAt"),
    }


def check_pr_violations(
    org_repo: str, pr_number: int, pr_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Check if PR has check violations.

    Args:
        org_repo: Repository in 'owner/repo' format
        pr_number: Pull request number
        pr_data: PR metadata from GitHub API

    Returns:
        Violation dict if failed checks found, None otherwise
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                org_repo,
                "--json",
                "statusCheckRollup",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PR_VIEW,
        )

        if result.returncode != 0:
            logger.warning(
                f"gh CLI failed for {org_repo}#{pr_number}: {result.stderr.strip()}"
            )
            return None

        data = json.loads(result.stdout)
        rollup = data.get("statusCheckRollup", [])

        all_failed = [
            {
                "name": check.get("name"),
                "conclusion": check.get("conclusion"),
                "url": check.get("detailsUrl", ""),
            }
            for check in rollup
            if check.get("conclusion") == "FAILURE"
        ]

        failed_checks = [c for c in all_failed if not is_security_scan(c.get("name"))]
        security_scans = [c for c in all_failed if is_security_scan(c.get("name"))]

        if failed_checks:
            result = {
                **extract_pr_metadata(pr_data),
                "failed_checks": failed_checks,
            }
            if security_scans:
                result["excluded_security_scans"] = security_scans
            return result

        if security_scans:
            logger.info(
                f"PR {org_repo}#{pr_number}: only security scan failures "
                f"({', '.join(c['name'] for c in security_scans)}), skipping"
            )

        return None

    except subprocess.TimeoutExpired:
        logger.warning(
            f"Timeout checking {org_repo}#{pr_number} after {TIMEOUT_PR_VIEW}s"
        )
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON from gh CLI for {org_repo}#{pr_number}: {e}")
        return None


def main():
    """Main entry point for merge violation checker."""
    now = datetime.now(timezone.utc)

    # Check capacity
    active_n, max_n = get_capacity()
    if active_n >= max_n:
        logger.info(f"At capacity ({active_n}/{max_n})")
        output_result("skip", f"At capacity ({active_n}/{max_n})")
        return

    # Check for existing merge violation tasks
    tasks = get_tasks()
    active_violations = [
        t
        for t in tasks
        if t.get("external_key", "").startswith("merge-violation:")
        and t.get("status") in ("in_progress", "pr_open", "pr_changes")
    ]
    if len(active_violations) >= MAX_CONCURRENT_VIOLATIONS:
        logger.info(f"Already processing {len(active_violations)} violations")
        output_result("skip", f"Already processing {len(active_violations)} violations")
        return

    repos = load_project_repos()

    scan_config = load_config()
    scan_only = scan_config.get("scan_only_repos") if scan_config else None
    if scan_only is not None:
        logger.info(f"Filtering to scan_only_repos: {scan_only}")
        repos = {k: v for k, v in repos.items() if k in scan_only}
        if not repos:
            logger.warning("No repos match scan_only_repos filter")
            output_result("skip", "No repositories configured in scan_only_repos")
            return

    violations: Dict[str, List[Dict[str, Any]]] = {}

    since = now - timedelta(hours=SCAN_INTERVAL_HOURS)

    logger.info(
        f"Checking {len(repos)} repositories for merge violations since {since}"
    )

    for repo_name in repos.keys():
        upstream, host = upstream_repo(repo_name)
        if not upstream or host != "github":
            continue

        try:
            # Get recent merges
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    upstream,
                    "--state",
                    "merged",
                    "--limit",
                    str(MAX_PRS_PER_REPO),
                    "--json",
                    "number,title,url,author,mergedAt",
                ],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_PR_LIST,
            )

            if result.returncode != 0:
                logger.warning(f"gh CLI failed for {upstream}: {result.stderr.strip()}")
                continue

            prs = json.loads(result.stdout)

            # Filter to recent PRs with validated timestamps
            recent_prs = []
            for pr in prs:
                merged_at = parse_merged_at(pr.get("mergedAt"))
                if merged_at and merged_at > since:
                    recent_prs.append(pr)

            logger.info(f"{upstream}: {len(recent_prs)} PRs merged since {since}")

            # Check each PR
            repo_violations = []
            for pr in recent_prs:
                violation = check_pr_violations(upstream, pr["number"], pr)
                if violation:
                    repo_violations.append(violation)

            if repo_violations:
                violations[repo_name] = repo_violations
                logger.info(f"{upstream}: Found {len(repo_violations)} violations")

        except subprocess.TimeoutExpired:
            logger.warning(
                f"Timeout listing PRs for {upstream} after {TIMEOUT_PR_LIST}s"
            )
            continue
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from gh CLI for {upstream}: {e}")
            continue

    if not violations:
        logger.info(f"No merged PRs with failed checks since {since}")
        output_result("skip", f"No merged PRs with failed checks since {since}")
        return

    all_violations = []
    for repo, prs in violations.items():
        for pr in prs:
            all_violations.append({**pr, "repo": repo})

    logger.info(
        f"Found {len(all_violations)} violations across {len(violations)} repositories"
    )

    content = f"# Merge Violations ({len(all_violations)} total)\n\nhigh:\n"
    for item in all_violations:
        merged_date = item["merged_at"][:10] if item["merged_at"] else "unknown"
        check_list = ", ".join(c["name"] for c in item["failed_checks"])
        content += (
            f"  - {item['repo']} #{item['number']}: {item['title']} "
            f"(@{item['author']}, {merged_date})\n"
            f"    failed: {check_list}\n"
        )

    output_result("start", content)


if __name__ == "__main__":
    main()
