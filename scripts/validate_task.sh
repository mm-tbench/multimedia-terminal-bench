#!/usr/bin/env bash
# Validate a task: build container, run oracle solution, run dummy (no solution).
#
# Usage:
#   ./scripts/validate_task.sh datasets/mmtb-core/my-task
#   ./scripts/validate_task.sh  # validates all tasks
#
# Checks:
#   1. Structure validation (no Docker)
#   2. Docker image builds
#   3. Oracle solution → reward.txt == 1
#   4. Dummy run (no solution) → reward.txt == 0
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}PASS${NC}  $1"; }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAILED=1; }
skip() { echo -e "  ${YELLOW}SKIP${NC}  $1"; }

validate_one_task() {
    local task_dir
    task_dir="$(cd "$1" && pwd)"
    local task_name
    task_name="$(basename "$task_dir")"
    local image_tag="mmtb-validate-${task_name}"
    local FAILED=0

    echo ""
    echo "=== ${task_name} ==="

    # --- Step 1: Structure check ---
    if uv run python "$SCRIPT_DIR/validate_task_structure.py" "$task_dir" > /dev/null 2>&1; then
        pass "Structure"
    else
        fail "Structure (run: uv run python scripts/validate_task_structure.py $task_dir)"
        return 1
    fi

    # --- Step 2: Docker build ---
    # Pass HF_TOKEN as a build-arg if set (used only when fetching from a
    # private HF mirror). Public mirror works anonymously, no token needed.
    local build_args=(-t "$image_tag" -f "$task_dir/environment/Dockerfile" "$task_dir/environment")
    if [ -n "${HF_TOKEN:-}" ]; then
        build_args=(--build-arg "HF_TOKEN=$HF_TOKEN" "${build_args[@]}")
    fi
    if docker build "${build_args[@]}" > /dev/null 2>&1; then
        pass "Docker build"
    else
        fail "Docker build"
        return 1
    fi

    # --- Step 3: Oracle solution passes ---
    local oracle_reward
    oracle_reward=$(docker run --rm \
        -v "$task_dir/tests:/tests:ro" \
        -v "$task_dir/solution:/solution:ro" \
        "$image_tag" \
        bash -c '
            mkdir -p /logs/verifier
            cd /app
            bash /solution/solve.sh > /dev/null 2>&1
            bash /tests/test.sh > /dev/null 2>&1 || true
            cat /logs/verifier/reward.txt 2>/dev/null || echo "MISSING"
        ' 2>/dev/null) || oracle_reward="ERROR"

    if [ "$oracle_reward" = "1" ]; then
        pass "Oracle solution → reward=1"
    else
        fail "Oracle solution → reward=${oracle_reward} (expected 1)"
    fi

    # --- Step 4: Dummy run fails ---
    local dummy_reward
    dummy_reward=$(docker run --rm \
        -v "$task_dir/tests:/tests:ro" \
        "$image_tag" \
        bash -c '
            mkdir -p /logs/verifier
            cd /app
            bash /tests/test.sh > /dev/null 2>&1 || true
            cat /logs/verifier/reward.txt 2>/dev/null || echo "MISSING"
        ' 2>/dev/null) || dummy_reward="ERROR"

    if [ "$dummy_reward" = "0" ]; then
        pass "Dummy run → reward=0"
    else
        fail "Dummy run → reward=${dummy_reward} (expected 0)"
    fi

    # --- Cleanup ---
    docker rmi "$image_tag" > /dev/null 2>&1 || true

    return $FAILED
}

# --- Main ---
TASKS=()
if [ $# -gt 0 ]; then
    for arg in "$@"; do
        TASKS+=("$arg")
    done
else
    for dir in "$REPO_ROOT"/datasets/mmtb-core/*/; do
        if [ -f "$dir/task.toml" ]; then
            TASKS+=("$dir")
        fi
    done
fi

if [ ${#TASKS[@]} -eq 0 ]; then
    echo "No tasks found."
    exit 1
fi

TOTAL=0
PASSED=0

for task in "${TASKS[@]}"; do
    TOTAL=$((TOTAL + 1))
    if validate_one_task "$task"; then
        PASSED=$((PASSED + 1))
    fi
done

echo ""
echo "=== Results: ${PASSED}/${TOTAL} tasks passed ==="
exit $((TOTAL - PASSED))
