#!/usr/bin/env python3
"""Tests for 01-check-merged-prs.py preflight script."""

import pytest
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def mock_common_module():
    """Mock the common module that preflight scripts depend on."""
    mock_common = Mock()
    mock_common.load_project_repos = Mock(return_value={})
    mock_common.upstream_repo = Mock(
        return_value=("RedHatInsights/test-repo", "github")
    )
    mock_common.output_result = Mock()
    mock_common.get_capacity = Mock(return_value=(0, 10))
    mock_common.get_tasks = Mock(return_value=[])
    sys.modules["common"] = mock_common

    yield mock_common

    # Cleanup
    if "common" in sys.modules:
        del sys.modules["common"]


# Import after mocking common
import importlib

spec = importlib.util.spec_from_file_location(
    "check_merged_prs", Path(__file__).parent.parent / "01-check-merged-prs.py"
)
check_module = importlib.util.module_from_spec(spec)


def make_gh_mock(list_response="[]", view_response=None):
    """Return a mock subprocess.run that dispatches gh pr list/view calls."""

    def mock_subprocess_run(cmd, **kwargs):
        result = Mock()
        result.returncode = 0

        if "list" in cmd:
            result.stdout = (
                json.dumps(list_response)
                if not isinstance(list_response, str)
                else list_response
            )
        elif "view" in cmd:
            result.stdout = (
                json.dumps(view_response) if view_response is not None else "{}"
            )

        return result

    return mock_subprocess_run


def run_main(gh_mock, scan_config=None):
    """Load the check module and run main() with patched subprocess and scan config."""
    spec.loader.exec_module(check_module)

    with (
        patch("subprocess.run", side_effect=gh_mock),
        patch.object(check_module, "load_config", return_value=scan_config),
    ):
        check_module.main()


@pytest.fixture
def sample_pr_data():
    """Sample PR data matching gh CLI output format."""
    return {
        "number": 123,
        "title": "Fix critical bug",
        "url": "https://github.com/RedHatInsights/test-repo/pull/123",
        "author": {"login": "developer"},
        "mergedAt": datetime.now().isoformat() + "Z",
    }


@pytest.fixture
def sample_status_checks():
    """Sample status check rollup data."""
    return {
        "statusCheckRollup": [
            {
                "name": "ci/test",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/actions/runs/123",
            },
            {
                "name": "ci/lint",
                "conclusion": "FAILURE",
                "detailsUrl": "https://github.com/actions/runs/124",
            },
            {
                "name": "ci/build",
                "conclusion": "CANCELLED",
                "detailsUrl": "https://github.com/actions/runs/125",
            },
        ]
    }


class TestCheckPrViolations:
    """Tests for check_pr_violations function."""

    def test_detects_failed_checks(self, sample_pr_data, sample_status_checks):
        """Detects PRs with failed status checks."""
        spec.loader.exec_module(check_module)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(sample_status_checks)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is not None
        assert result["number"] == 123
        assert result["title"] == "Fix critical bug"
        assert len(result["failed_checks"]) == 1  # FAILURE only

        # Verify failed checks
        failed_names = {c["name"] for c in result["failed_checks"]}
        assert "ci/lint" in failed_names  # FAILURE
        # ci/build (CANCELLED) is no longer flagged

    def test_ignores_skipped_checks(self, sample_pr_data):
        """Ignores PRs with only skipped status checks."""
        spec.loader.exec_module(check_module)

        status_with_skip = {
            "statusCheckRollup": [
                {
                    "name": "optional-check",
                    "conclusion": "SKIPPED",
                    "detailsUrl": "https://github.com/actions/runs/126",
                }
            ]
        }

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(status_with_skip)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        # SKIPPED checks are now tolerated
        assert result is None

    def test_returns_none_for_all_passing(self, sample_pr_data):
        """Returns None when all checks pass."""
        spec.loader.exec_module(check_module)

        all_passing = {
            "statusCheckRollup": [
                {
                    "name": "ci/test",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://github.com/actions/runs/123",
                },
                {
                    "name": "ci/lint",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://github.com/actions/runs/124",
                },
            ]
        }

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(all_passing)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is None

    def test_handles_gh_cli_error(self, sample_pr_data):
        """Handles gh CLI errors gracefully."""
        spec.loader.exec_module(check_module)

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is None

    def test_handles_timeout(self, sample_pr_data):
        """Handles subprocess timeout gracefully."""
        spec.loader.exec_module(check_module)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 10)):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is None

    def test_handles_invalid_json(self, sample_pr_data):
        """Handles invalid JSON response gracefully."""
        spec.loader.exec_module(check_module)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is None

    def test_includes_pr_metadata(self, sample_pr_data, sample_status_checks):
        """Includes all relevant PR metadata in result."""
        spec.loader.exec_module(check_module)

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(sample_status_checks)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result["number"] == 123
        assert result["title"] == "Fix critical bug"
        assert result["url"] == "https://github.com/RedHatInsights/test-repo/pull/123"
        assert result["author"] == "developer"
        assert "merged_at" in result


class TestSecurityScanFiltering:
    """Tests for security scan exclusion in check_pr_violations."""

    def test_security_scan_excluded_from_violations(self, sample_pr_data):
        """Security scan failures should not appear in failed_checks."""
        spec.loader.exec_module(check_module)

        status_checks = {
            "statusCheckRollup": [
                {"name": "ci/test", "conclusion": "FAILURE", "detailsUrl": "url1"},
                {"name": "clair-scan", "conclusion": "FAILURE", "detailsUrl": "url2"},
            ]
        }

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(status_checks)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is not None
        failed_names = {c["name"] for c in result["failed_checks"]}
        assert "ci/test" in failed_names
        assert "clair-scan" not in failed_names
        assert len(result["excluded_security_scans"]) == 1
        assert result["excluded_security_scans"][0]["name"] == "clair-scan"

    def test_only_security_scan_failures_no_violation(self, sample_pr_data):
        """When only security scans fail, no violation should be reported."""
        spec.loader.exec_module(check_module)

        status_checks = {
            "statusCheckRollup": [
                {"name": "clair-scan", "conclusion": "FAILURE", "detailsUrl": "url1"},
                {
                    "name": "sast-snyk-check",
                    "conclusion": "FAILURE",
                    "detailsUrl": "url2",
                },
            ]
        }

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(status_checks)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is None

    def test_mixed_failures_only_ci_in_violation(self, sample_pr_data):
        """Mix of CI and security scan failures should only include CI checks."""
        spec.loader.exec_module(check_module)

        status_checks = {
            "statusCheckRollup": [
                {"name": "ci/lint", "conclusion": "FAILURE", "detailsUrl": "url1"},
                {"name": "ci/build", "conclusion": "FAILURE", "detailsUrl": "url2"},
                {
                    "name": "grype-vulnerability-scan",
                    "conclusion": "FAILURE",
                    "detailsUrl": "url3",
                },
                {"name": "trivy-scan", "conclusion": "FAILURE", "detailsUrl": "url4"},
            ]
        }

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(status_checks)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is not None
        failed_names = {c["name"] for c in result["failed_checks"]}
        assert failed_names == {"ci/lint", "ci/build"}
        assert len(result["excluded_security_scans"]) == 2

    def test_no_security_scans_unchanged(self, sample_pr_data):
        """When no security scans are present, behavior is unchanged."""
        spec.loader.exec_module(check_module)

        status_checks = {
            "statusCheckRollup": [
                {"name": "ci/lint", "conclusion": "FAILURE", "detailsUrl": "url1"},
            ]
        }

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(status_checks)

        with patch("subprocess.run", return_value=mock_result):
            result = check_module.check_pr_violations(
                "RedHatInsights/test-repo", 123, sample_pr_data
            )

        assert result is not None
        assert len(result["failed_checks"]) == 1
        assert "excluded_security_scans" not in result


class TestMainFunction:
    """Integration tests for main() function."""

    def test_scans_with_no_repos(self, mock_common_module):
        """Scans and skips when no repos have violations."""
        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.get_tasks.return_value = []
        mock_common_module.load_project_repos.return_value = {}

        spec.loader.exec_module(check_module)
        check_module.main()

        mock_common_module.output_result.assert_called_once()
        call_args = mock_common_module.output_result.call_args[0]
        assert call_args[0] == "skip"

    def test_skips_at_capacity(self, mock_common_module):
        """Skips scan when at capacity."""
        mock_common_module.get_capacity.return_value = (10, 10)

        spec.loader.exec_module(check_module)
        check_module.main()

        mock_common_module.output_result.assert_called_once()
        call_args = mock_common_module.output_result.call_args[0]
        assert call_args[0] == "skip"
        assert "capacity" in call_args[1].lower()

    def test_skips_when_too_many_violations(self, mock_common_module):
        """Skips when already processing too many violations."""
        mock_common_module.get_capacity.return_value = (0, 10)

        # Mock 5 active violation tasks
        mock_common_module.get_tasks.return_value = [
            {"external_key": f"merge-violation:repo{i}:123", "status": "in_progress"}
            for i in range(5)
        ]

        spec.loader.exec_module(check_module)
        check_module.main()

        mock_common_module.output_result.assert_called_once()
        call_args = mock_common_module.output_result.call_args[0]
        assert call_args[0] == "skip"
        assert "processing" in call_args[1].lower()

    def test_processes_violations(self, mock_common_module):
        """Processes violations and generates output."""
        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.get_tasks.return_value = []
        mock_common_module.load_project_repos.return_value = {
            "test-repo": {"upstream": "https://github.com/RedHatInsights/test-repo"}
        }
        mock_common_module.upstream_repo.return_value = (
            "RedHatInsights/test-repo",
            "github",
        )

        # Mock gh pr list response (recent PR with timezone-aware timestamp)
        from datetime import timezone

        recent_pr = {
            "number": 123,
            "title": "Fix critical bug",
            "url": "https://github.com/RedHatInsights/test-repo/pull/123",
            "author": {"login": "developer"},
            "mergedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        pr_list_response = [recent_pr]

        # Mock gh pr view response with violations
        pr_view_response = {
            "statusCheckRollup": [
                {
                    "name": "ci/test",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://github.com/actions/runs/123",
                }
            ]
        }

        run_main(make_gh_mock(pr_list_response, pr_view_response))

        # Verify output was called with violations
        mock_common_module.output_result.assert_called_once()
        call_args = mock_common_module.output_result.call_args[0]
        assert call_args[0] == "start"
        assert "violation" in call_args[1].lower()

    def test_skips_non_github_repos(self, mock_common_module):
        """Skips repositories not hosted on GitHub."""
        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.load_project_repos.return_value = {
            "gitlab-repo": {"upstream": "https://gitlab.com/test/repo"}
        }
        mock_common_module.upstream_repo.return_value = (
            "test/repo",
            "gitlab",  # Non-GitHub host
        )

        spec.loader.exec_module(check_module)
        check_module.main()

        # Should skip and output "no violations"
        mock_common_module.output_result.assert_called_once()
        call_args = mock_common_module.output_result.call_args[0]
        assert call_args[0] == "skip"

    def test_filters_by_scan_window(self, mock_common_module):
        """Only processes PRs merged within the 24-hour lookback window."""
        from datetime import timezone

        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.load_project_repos.return_value = {
            "test-repo": {"upstream": "https://github.com/RedHatInsights/test-repo"}
        }
        mock_common_module.upstream_repo.return_value = (
            "RedHatInsights/test-repo",
            "github",
        )

        # Create PRs: one recent, one old (both timezone-aware)
        recent_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        old_time = (
            (datetime.now(timezone.utc) - timedelta(hours=48))
            .isoformat()
            .replace("+00:00", "Z")
        )

        pr_list_response = [
            {
                "number": 123,
                "title": "Recent PR",
                "url": "https://github.com/test/pull/123",
                "author": {"login": "dev"},
                "mergedAt": recent_time,
            },
            {
                "number": 122,
                "title": "Old PR",
                "url": "https://github.com/test/pull/122",
                "author": {"login": "dev"},
                "mergedAt": old_time,
            },
        ]

        violations_response = {
            "statusCheckRollup": [
                {"name": "test", "conclusion": "FAILURE", "detailsUrl": "url"}
            ]
        }

        view_call_count = 0

        def mock_subprocess_run(cmd, **kwargs):
            nonlocal view_call_count
            result = Mock()
            result.returncode = 0

            if "list" in cmd:
                result.stdout = json.dumps(pr_list_response)
            elif "view" in cmd:
                view_call_count += 1
                result.stdout = json.dumps(violations_response)

            return result

        run_main(mock_subprocess_run)

        # Should only check the recent PR (within default scan window)
        assert view_call_count == 1


class TestScanOnlyReposFilter:
    """Tests for scan_only_repos whitelist filtering."""

    def test_filters_repos_to_whitelist(self, mock_common_module):
        """Only scans repos listed in scan_only_repos."""
        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.get_tasks.return_value = []
        mock_common_module.load_project_repos.return_value = {
            "insights-chrome": {
                "upstream": "https://github.com/RedHatInsights/insights-chrome"
            },
            "insights-inventory-frontend": {
                "upstream": "https://github.com/RedHatInsights/insights-inventory-frontend"
            },
            "landing-page-frontend": {
                "upstream": "https://github.com/RedHatInsights/landing-page-frontend"
            },
        }

        def upstream_side_effect(repo_name):
            return (f"RedHatInsights/{repo_name}", "github")

        mock_common_module.upstream_repo.side_effect = upstream_side_effect

        scanned_repos = []

        def mock_subprocess_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            if "list" in cmd:
                repo_flag_idx = cmd.index("--repo") + 1
                scanned_repos.append(cmd[repo_flag_idx])
                result.stdout = "[]"
            else:
                result.stdout = "[]"
            return result

        scan_config = {
            "scan_only_repos": ["insights-chrome", "landing-page-frontend"],
        }

        run_main(mock_subprocess_run, scan_config)

        assert "RedHatInsights/insights-chrome" in scanned_repos
        assert "RedHatInsights/landing-page-frontend" in scanned_repos
        assert "RedHatInsights/insights-inventory-frontend" not in scanned_repos

    def test_scans_all_repos_when_no_config(self, mock_common_module):
        """Scans all repos when test-config.yaml is missing."""
        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.get_tasks.return_value = []
        mock_common_module.load_project_repos.return_value = {
            "repo-a": {"upstream": "https://github.com/Org/repo-a"},
            "repo-b": {"upstream": "https://github.com/Org/repo-b"},
        }

        def upstream_side_effect(repo_name):
            return (f"Org/{repo_name}", "github")

        mock_common_module.upstream_repo.side_effect = upstream_side_effect

        scanned_repos = []

        def mock_subprocess_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            if "list" in cmd:
                repo_flag_idx = cmd.index("--repo") + 1
                scanned_repos.append(cmd[repo_flag_idx])
                result.stdout = "[]"
            else:
                result.stdout = "[]"
            return result

        run_main(mock_subprocess_run)

        assert len(scanned_repos) == 2

    def test_skips_when_whitelist_matches_no_repos(self, mock_common_module):
        """Skips with message when scan_only_repos matches nothing."""
        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.get_tasks.return_value = []
        mock_common_module.load_project_repos.return_value = {
            "repo-a": {"upstream": "https://github.com/Org/repo-a"},
        }

        scan_config = {
            "scan_only_repos": ["nonexistent-repo"],
        }

        spec.loader.exec_module(check_module)

        with patch.object(check_module, "load_config", return_value=scan_config):
            check_module.main()

        mock_common_module.output_result.assert_called_once()
        call_args = mock_common_module.output_result.call_args[0]
        assert call_args[0] == "skip"
        assert "scan_only_repos" in call_args[1]


class TestSeverityAssessment:
    """Tests for severity assessment in violation output."""

    def test_failure_is_high_severity(self, mock_common_module):
        """FAILURE conclusions are marked as HIGH severity."""
        from datetime import timezone

        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.load_project_repos.return_value = {
            "test-repo": {"upstream": "https://github.com/RedHatInsights/test-repo"}
        }
        mock_common_module.upstream_repo.return_value = (
            "RedHatInsights/test-repo",
            "github",
        )

        recent_pr = {
            "number": 123,
            "title": "Fix critical bug",
            "url": "https://github.com/RedHatInsights/test-repo/pull/123",
            "author": {"login": "developer"},
            "mergedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        pr_list_response = [recent_pr]
        pr_view_response = {
            "statusCheckRollup": [
                {"name": "test", "conclusion": "FAILURE", "detailsUrl": "url"}
            ]
        }

        run_main(make_gh_mock(pr_list_response, pr_view_response))

        call_args = mock_common_module.output_result.call_args[0]
        # Check for lowercase "high:" in new compact format
        assert "high:" in call_args[1]

    def test_cancelled_is_ignored(self, mock_common_module):
        """CANCELLED conclusions are now tolerated and ignored."""
        from datetime import timezone

        mock_common_module.get_capacity.return_value = (0, 10)
        mock_common_module.load_project_repos.return_value = {
            "test-repo": {"upstream": "https://github.com/RedHatInsights/test-repo"}
        }
        mock_common_module.upstream_repo.return_value = (
            "RedHatInsights/test-repo",
            "github",
        )

        recent_pr = {
            "number": 123,
            "title": "Fix critical bug",
            "url": "https://github.com/RedHatInsights/test-repo/pull/123",
            "author": {"login": "developer"},
            "mergedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        pr_list_response = [recent_pr]
        pr_view_response = {
            "statusCheckRollup": [
                {"name": "test", "conclusion": "CANCELLED", "detailsUrl": "url"}
            ]
        }

        run_main(make_gh_mock(pr_list_response, pr_view_response))

        call_args = mock_common_module.output_result.call_args[0]
        # CANCELLED checks are now tolerated, so should skip
        assert "No merged PRs with failed checks since" in call_args[1]


class TestLoadConfig:
    """Tests for shared config loader."""

    def test_returns_none_on_missing_file(self, tmp_path):
        """Returns None when config file doesn't exist."""
        from lib import config

        with patch.object(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml"):
            assert config.load_config() is None

    def test_returns_none_on_invalid_yaml(self, tmp_path):
        """Returns None when config file contains invalid YAML."""
        from lib import config

        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(":\n  - :\n  bad: [unterminated")

        with patch.object(config, "CONFIG_PATH", bad_file):
            assert config.load_config() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
