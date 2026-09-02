"""Security scan detection for CI check filtering."""

SECURITY_SCAN_PATTERNS = (
    "clair-scan",
    "sast-",
    "clamav-scan",
    "coverity",
    "rpms-signature-scan",
    "deprecated-image-check",
    "ecosystem-cert-preflight",
    "grype",
    "trivy",
    "snyk",
    "vulnerability",
    "security-scan",
    "cve-scan",
    "container-scan",
    "image-scan",
)


def is_security_scan(check_name):
    """Check if a CI check name matches a known security scan pattern."""
    if not check_name:
        return False
    name_lower = check_name.lower()
    return any(pattern in name_lower for pattern in SECURITY_SCAN_PATTERNS)
