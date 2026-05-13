#!/usr/bin/env bash
# Run MMTB tasks with Claude Code authenticated via Claude.ai OAuth (Max subscription).
#
# Auth model:
#   ~/.claude/.credentials.json holds an OAuth token bundle from `claude login`
#   (subscriptionType=max, rateLimitTier=default_claude_max_20x). Harbor's
#   built-in claude-code agent already reads CLAUDE_CODE_OAUTH_TOKEN from env;
#   we extract the accessToken from the credentials file and pass it through.
#
#   ANTHROPIC_API_KEY is explicitly UNSET so the agent does not fall back to
#   the API path (the user's Anthropic Console plan is Free / no credit;
#   the $200 Additional Usage overflow lives behind OAuth only).
#
# Usage:
#   ./scripts/run_claude_code_oauth.sh -p datasets/mmtb-core/<task> \
#       -m anthropic/claude-sonnet-4-6 [harbor flags...]
#
# Notes:
# - Local docker only (the host's ~/.claude/ is not visible to Daytona sandboxes).
# - The accessToken expires periodically; refreshToken refresh requires
#   re-running `claude` on the host. If a sweep runs longer than the token TTL,
#   re-extract the token and resume from cells without reward.json.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

CRED_FILE="${CLAUDE_CREDENTIALS_FILE:-$HOME/.claude/.credentials.json}"
if [ ! -f "$CRED_FILE" ]; then
    echo "Error: $CRED_FILE not found." >&2
    echo "Run 'claude login' once on this host to populate it." >&2
    exit 1
fi

# Extract accessToken via python (avoid jq dependency).
ACCESS_TOKEN="$(python3 -c "
import json, sys, time
j = json.load(open('$CRED_FILE'))
o = j.get('claudeAiOauth') or {}
tok = o.get('accessToken')
exp_ms = o.get('expiresAt') or 0
now_ms = int(time.time() * 1000)
if not tok:
    sys.stderr.write('No accessToken in $CRED_FILE\n'); sys.exit(2)
if exp_ms and exp_ms < now_ms:
    delta_min = (now_ms - exp_ms) / 60000
    sys.stderr.write(f'Warning: accessToken expired {delta_min:.0f} min ago — Claude Code may fail with auth error.\n')
elif exp_ms:
    delta_min = (exp_ms - now_ms) / 60000
    sys.stderr.write(f'accessToken valid for {delta_min:.0f} more minutes.\n')
print(tok)
")"
[ -n "$ACCESS_TOKEN" ] || { echo "Error: empty accessToken extracted." >&2; exit 1; }

# Load .env (for any other Harbor settings; do NOT pull ANTHROPIC_API_KEY).
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

# Explicitly unset ANTHROPIC_API_KEY so Harbor's claude-code agent won't
# include it in the container's env. (See claude_code.py line 943-953.)
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN

export CLAUDE_CODE_OAUTH_TOKEN="$ACCESS_TOKEN"

exec harbor run \
    -a claude-code \
    "$@"
