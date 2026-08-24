# Quality Monitor Deployment Guide

## Overview

The quality-monitor instance uses the existing `deploy/template.yaml` with specific parameters. No Konflux changes needed - just add a new deployment target in app-interface.

## Prerequisites

- PR merged to `RedHatInsights/hcc-framework-agent-dev:master`
- Konflux has rebuilt the image
- Access to app-interface repository

`IMAGE_DIGEST` does not need to be looked up manually — app-interface's
`openshift-saas-deploy` auto-resolves it from Quay at deploy time (via the
template's `REGISTRY_IMG`/`IMAGE_TAG` parameters), the same way it already
auto-resolves `IMAGE_TAG` from the git ref today. Leave it unset in the
target's `parameters` unless you're intentionally pinning to a specific
historical build.

## App-Interface Configuration

### 1. Locate the SaaS File

Find the app-interface file that deploys other bot instances (likely `data/services/insights/platform-frontend-ai-dev/saas-*.yml`)

### 2. Add New ResourceTemplate

Add this resourceTemplate to the existing file:

```yaml
resourceTemplates:
  # ... existing instances (framework-config, manager-tasks) ...

  - name: devbot-quality-monitor
    url: https://github.com/RedHatInsights/hcc-framework-agent-dev
    path: /deploy/template.yaml
    targets:
      - namespace:
          $ref: /services/insights/platform-frontend-ai-dev/namespaces/stage.hcmais01ue1.yml
        ref: <COMMIT_SHA>  # SHA from merged PR
        parameters:
          # IMAGE_DIGEST intentionally omitted — auto-resolved by app-interface
          # from REGISTRY_IMG/IMAGE_TAG (template defaults) at deploy time.
          BOT_IMAGE: quay.io/redhat-services-prod/hcc-platex-services/hcc-framework-agent-dev
          BOT_NAME: devbot-quality
          BOT_INSTANCE_ID: quality-monitor
          BOT_CONFIG_PATH: instance/quality-monitor
          BOT_CONFIG_REPO: https://github.com/RedHatInsights/hcc-framework-agent-dev.git
          BOT_LABEL: quality-monitor
          BOT_REPLICAS: '0'  # KEDA scales from 0
          GCP_PROJECT_ID: <EXISTING_GCP_PROJECT>  # Same as other instances
          GCP_REGION: global
          VERTEX_ALLOWED_MODELS: claude-sonnet-4-6,claude-opus-4-6
          SLACK_WEBHOOK_URL: <YOUR_SLACK_WEBHOOK>
          KEDA_TIMEZONE: America/New_York
          KEDA_CRON_START: "0 9 * * 1-5"  # 9 AM weekdays
          KEDA_CRON_END: "0 10 * * 1-5"   # Runs for 1 hour window
```

### 3. Parameter Details

**Required (unique to quality-monitor):**
- `BOT_NAME: devbot-quality` - Deployment name
- `BOT_INSTANCE_ID: quality-monitor` - Instance identifier
- `BOT_CONFIG_PATH: instance/quality-monitor` - Points to config directory
- `KEDA_CRON_START: "0 9 * * 1-5"` - Daily 9 AM ET, Monday-Friday
- `KEDA_CRON_END: "0 10 * * 1-5"` - 1-hour window

**Shared (copy from existing instances):**
- `GCP_PROJECT_ID` - Same as framework-config
- `GCP_REGION: global`
- `VERTEX_ALLOWED_MODELS` - Same as other instances
- `ref` - Same commit SHA
- `IMAGE_DIGEST` - do not set manually; auto-resolved by app-interface at deploy time (see Prerequisites)

**Optional:**
- `SLACK_WEBHOOK_URL` - For notifications (recommended)
- `KEDA_TIMEZONE` - Adjust to your team's timezone

### 4. KEDA Schedule Options

**Default (9 AM ET, weekdays only):**
```yaml
KEDA_TIMEZONE: America/New_York
KEDA_CRON_START: "0 9 * * 1-5"
KEDA_CRON_END: "0 10 * * 1-5"
```

**Daily including weekends:**
```yaml
KEDA_CRON_START: "0 9 * * *"
KEDA_CRON_END: "0 10 * * *"
```

**Twice daily:**
```yaml
# Morning
KEDA_CRON_START: "0 9 * * 1-5"
KEDA_CRON_END: "0 10 * * 1-5"

# Afternoon (requires second ScaledObject - contact platform team)
# KEDA_CRON_START: "0 15 * * 1-5"
# KEDA_CRON_END: "0 16 * * 1-5"
```

## Verification Steps

### 1. Check Deployment Created

```bash
oc get deployment devbot-quality -n <namespace>
```

Expected output:
```
NAME             READY   UP-TO-DATE   AVAILABLE   AGE
devbot-quality   0/0     0            0           1m
```

(0/0 is normal - KEDA scales from 0)

### 2. Verify Configuration

```bash
oc get deployment devbot-quality -o yaml | grep -A 5 BOT_CONFIG_PATH
```

Should show:
```yaml
- name: BOT_CONFIG_PATH
  value: instance/quality-monitor
```

### 3. Check KEDA Scaler

```bash
oc get scaledobject devbot-quality-cron-scaler -o yaml
```

Verify:
- `timezone: America/New_York`
- `start: "0 9 * * 1-5"`
- `desiredReplicas: "1"`

### 4. Check NetworkPolicy

```bash
oc get networkpolicy devbot-quality-egress
```

Should exist and allow egress to proxy + memory-server.

### 5. Wait for Scheduled Time

At the scheduled time (9 AM ET), verify:

```bash
# Pod should scale up
oc get pods -l app.kubernetes.io/name=devbot-quality

# Check logs
oc logs -f deployment/devbot-quality
```

### 6. Check Memory Server

```bash
# Via port-forward
oc port-forward svc/devbot-memory-server 8080:8080

# Visit http://localhost:8080
# Look for quality-monitor instance
```

## Troubleshooting

### Pod Not Scaling at Scheduled Time

**Check KEDA scaler:**
```bash
oc describe scaledobject devbot-quality-cron-scaler
```

**Verify timezone:**
```bash
oc get scaledobject devbot-quality-cron-scaler -o jsonpath='{.spec.triggers[0].metadata.timezone}'
```

**Check KEDA operator logs:**
```bash
oc logs -n keda deploy/keda-operator
```

### Pod Fails to Start

**Check image pull:**
```bash
oc describe deployment devbot-quality | grep -A 5 Image
```

**Check events:**
```bash
oc get events --field-selector involvedObject.name=devbot-quality
```

**Common issues:**
- Wrong `IMAGE_DIGEST` - verify the resolved digest matches Quay for the target `ref`'s commit (check the `openshift-saas-deploy` job output, not the saas file — value isn't set there)
- Missing secrets - verify `devbot-secrets` exists
- Network policy blocking - check `devbot-quality-egress`

### No JIRA Tickets Created

**Check logs for JIRA MCP connection:**
```bash
oc logs deployment/devbot-quality | grep -i jira
```

**Verify JIRA_MCP_URL:**
```bash
oc get deployment devbot-quality -o yaml | grep JIRA_MCP_URL
```

Should be: `http://devbot-proxy:8444/mcp`

**Check proxy pod:**
```bash
oc logs deployment/devbot-proxy | grep 8444
```

### Preflight Scripts Not Finding Work

**Check state persistence:**
```bash
# Logs should show state checks
oc logs deployment/devbot-quality | grep "Already scanned"
```

**Manually trigger (for testing):**
```bash
# Scale to 1 replica outside schedule
oc scale deployment devbot-quality --replicas=1

# Check logs
oc logs -f deployment/devbot-quality

# Scale back to 0 when done
oc scale deployment devbot-quality --replicas=0
```

## Rollback

If issues occur, scale to 0 and remove from app-interface:

```bash
# Immediate stop
oc scale deployment devbot-quality --replicas=0

# Remove from app-interface
# Delete the resourceTemplate entry
# Create MR to remove
```

## Monitoring

**Check daily runs:**
```bash
# Pod logs from last run
oc logs deployment/devbot-quality --previous

# Check memory server for tasks created
curl http://devbot-memory-server:8080/api/tasks?instance_id=quality-monitor
```

**Track metrics:**
- Number of violations detected (check logs)
- JIRA tickets created (query JIRA: `labels = quality`)
- Execution time (check pod logs)
- Failures (check pod restarts)

## Configuration Updates

To update configuration after deployment:

1. **Update instance files** - Edit `instance/quality-monitor/` files
2. **Commit to main** - PR and merge
3. **Wait for Konflux** - New image built
4. **Update app-interface** - Change `ref` to the new commit SHA (`IMAGE_DIGEST` re-resolves automatically)
5. **Verify** - Wait for next scheduled run

**For urgent config changes:**
- KEDA schedule can be updated in app-interface immediately
- Other env vars require image rebuild (or manual pod restart with env override)
