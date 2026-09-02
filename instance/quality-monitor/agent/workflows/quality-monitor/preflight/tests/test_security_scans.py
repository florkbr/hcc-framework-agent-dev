#!/usr/bin/env python3
"""Tests for lib/security_scans.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.security_scans import is_security_scan


class TestIsSecurityScan:
    """Tests for is_security_scan function."""

    def test_known_scan_patterns(self):
        assert is_security_scan("clair-scan") is True
        assert is_security_scan("sast-snyk-check") is True
        assert is_security_scan("sast-coverity-check") is True
        assert is_security_scan("clamav-scan") is True
        assert is_security_scan("grype") is True
        assert is_security_scan("trivy-scan") is True
        assert is_security_scan("rpms-signature-scan") is True
        assert is_security_scan("deprecated-image-check") is True
        assert is_security_scan("container-scan") is True
        assert is_security_scan("image-scan-results") is True

    def test_case_insensitive(self):
        assert is_security_scan("Clair-Scan") is True
        assert is_security_scan("SAST-SNYK-CHECK") is True
        assert is_security_scan("Grype") is True
        assert is_security_scan("TRIVY-SCAN") is True

    def test_non_security_checks(self):
        assert is_security_scan("unit-tests") is False
        assert is_security_scan("lint") is False
        assert is_security_scan("e2e-tests") is False
        assert is_security_scan("build") is False
        assert is_security_scan("integration-test") is False
        assert is_security_scan("ci/test") is False
        assert is_security_scan("ci/lint") is False

    def test_substring_match(self):
        assert is_security_scan("my-repo-grype-scan") is True
        assert is_security_scan("project-sast-snyk-check") is True
        assert is_security_scan("custom-security-scan-job") is True

    def test_empty_and_falsy(self):
        assert is_security_scan("") is False
        assert is_security_scan(None) is False
        assert is_security_scan("?") is False
