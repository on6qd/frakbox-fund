#!/bin/bash
# Unified daemon — research sessions, trade execution, and health monitoring.
# Usage: ./researcher.sh          (foreground)
#        nohup ./researcher.sh &  (background)
#
# Runs three tasks on different intervals from a single process:
#   - Trade loop:    every 2 min  (stop-losses, triggers, reconciliation)
#   - Health check:  every 10 min (watchdog, alerts, auto-restart)
#   - Research:      every 15 min (LLM sessions, 50 min each)

set -euo pipefail
cd "$(dirname "$0")"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

LOCKFILE="${TMPDIR:-/tmp}/research_bot_$(id -u).lock"
SESSION_INTERVAL=5400      # 90 min between research sessions (spreads 10 sessions across ~15h)
TRADE_INTERVAL=120         # 2 min between trade loop runs
HEALTH_INTERVAL=600        # 10 min between health checks
EXPORT_INTERVAL=3600       # 1 hour between dashboard exports
TICK=60                    # main loop tick (1 min)

set -a; source .env; set +a
MAX_SESSIONS_PER_DAY="${MAX_SESSIONS_PER_DAY:-10}"  # hard cap from .env
source venv/bin/activate 2>/dev/null || true

# Timestamps for interval tracking
last_trade=0
last_health=0
last_session=0
last_export=0
digest_sent=0

now() { date +%s; }

check_daily_limit() {
  local count_file="${TMPDIR:-/tmp}/research_sessions_$(date +%Y%m%d)_$(id -u).count"
  local count=0
  if [ -f "$count_file" ]; then
    count=$(cat "$count_file")
  fi
  if [ "$count" -ge "$MAX_SESSIONS_PER_DAY" ]; then
    echo "$(date): Daily session limit ($MAX_SESSIONS_PER_DAY) reached. Skipping."
    return 1
  fi
  echo $((count + 1)) > "$count_file"
  return 0
}

run_trade_loop() {
  python3 trade_loop.py >> logs/trade_loop.log 2>&1 || true
}

run_health_check() {
  python3 health_check.py >> logs/health_check.log 2>&1 || true
}

run_session() {
  local timestamp=$(date +%Y-%m-%d_%H%M)
  local logfile="logs/${timestamp}.log"
  mkdir -p logs

  # Skip if already running (don't consume daily limit for lock-skips)
  if ! mkdir "$LOCKFILE" 2>/dev/null; then
    echo "$(date): Session already running, skipping."
    return
  fi
  trap 'rmdir "$LOCKFILE" 2>/dev/null' RETURN

  # Check daily session limit (only after acquiring lock; trap ensures cleanup)
  if ! check_daily_limit; then
    echo "=== Heartbeat $(date) ===" >> logs/daemon.log
    return
  fi

  # Alternate between scan and investigate sessions
  # Count today's sessions to determine mode
  local count_file="${TMPDIR:-/tmp}/research_sessions_$(date +%Y%m%d)_$(id -u).count"
  local session_num=1
  if [ -f "$count_file" ]; then
    session_num=$(cat "$count_file")
  fi

  local agent="orchestrator"
  local mode="investigate"
  local timeout_min=50
  local model=""
  local prompt

  if (( session_num % 3 == 0 )); then
    # Every 3rd session is a scan
    agent="scanner"
    mode="scan"
    timeout_min=25
    model="claude-haiku-4-5-20251001"
    prompt=$(cat <<'PROMPT'
High-throughput scan session. Run 30+ quick statistical tests using data_tasks.py commands.

Quick context check: python3 run.py --context | head -80
Then check what's already been scanned: python3 -c "import db; db.init_db(); rows=db.get_db().execute(\"SELECT question FROM research_queue WHERE category='scan_hit' ORDER BY rowid DESC LIMIT 10\").fetchall(); [print(r[0][:120]) for r in rows]"

Pick a scan theme that hasn't been done recently. Run as many tests as possible. Queue any p<0.05 hits to research_queue.

Tool-call budget: ~60 calls. Track roughly — after ~50 calls, stop scanning, write the journal entry, and exit. Do not keep running tests past budget. Writing handoff for the next session is cheaper than a bloated session.

When done, log a journal entry with session_type="scan" and public_summary describing what you screened and how many hits.
PROMPT
    )
  else
    # Regular orchestrator session
    prompt=$(cat <<'PROMPT'
Run: python3 run.py --context
This is your complete state — account, trades, hypotheses, knowledge, queue, journal, friction, and data integrity. Steer.md (human directions) is included. Prioritize human directions over your own queue.

Check for scan hits first: python3 -c "import db; db.init_db(); rows=db.get_db().execute(\"SELECT id, question, priority FROM research_queue WHERE category='scan_hit' AND status='pending' ORDER BY priority DESC LIMIT 5\").fetchall(); [print(f'{r[0]}: {r[1][:120]}') for r in rows]"
If there are scan hits with priority >= 8, investigate the top one using the full 6-step investigation method.

Do NOT dump full datasets (load_hypotheses(), load_knowledge(), load_queue()). Only query individual items when you need deep detail.

Read API_REFERENCE.md only when you need a function signature — not at session start.

You have ~50 minutes AND a tool-call budget of ~120 calls. Each tool call re-reads the full cached context, so long sessions are disproportionately expensive. Pick ONE investigation per session. Only chain a second if the first resolved in under 60 calls. After ~100 calls, stop new work: write the handoff, log the journal entry, commit, and exit. A deferred next_step is cheaper than a 250-call monster session.

Commit to git after each significant finding.

Do the work. When done:
1. Update research_queue with handoff for the next session
2. Log journal entry: db.append_journal_entry(date, type, investigated, findings, surprised_by, next_step, public_summary="1-2 plain-English sentences summarizing what you found, for a public audience. No jargon, no IDs, no filenames.")
3. Commit to git
PROMPT
    )
  fi

  echo "=== Session started $(date) [mode=$mode agent=$agent model=${model:-default} session#$session_num] ===" | tee -a logs/daemon.log | tee "$logfile"

  if command -v gtimeout &>/dev/null; then
    TIMEOUT_CMD="gtimeout ${timeout_min}m"
  elif command -v timeout &>/dev/null; then
    TIMEOUT_CMD="timeout ${timeout_min}m"
  else
    TIMEOUT_CMD=""
  fi

  local exit_code=0
  $TIMEOUT_CMD claude \
    --agent "$agent" \
    ${model:+--model "$model"} \
    --dangerously-skip-permissions \
    --verbose \
    --output-format stream-json \
    -p "$prompt" < /dev/null >>"$logfile" 2>&1 || exit_code=$?

  echo "=== Session finished $(date) (exit code: $exit_code) ===" >> "$logfile"
  echo "=== Session finished $(date) (exit code: $exit_code) ===" >> logs/daemon.log

  # Determine session status from exit code
  local status="completed"
  if [ $exit_code -eq 124 ]; then
    status="timed_out"
  elif [ $exit_code -ne 0 ]; then
    status="crashed"
  fi

  # Log token usage from the session to SQLite
  python3 -c "
from email_report import parse_token_usage
import db
db.init_db()
usage = parse_token_usage('$logfile')
if usage.get('total_tokens', 0) > 0:
    db.append_token_usage(
        input_tokens=usage.get('input_tokens', 0),
        output_tokens=usage.get('output_tokens', 0),
        cache_read_tokens=usage.get('cache_read_tokens', 0),
        cache_creation_tokens=usage.get('cache_creation_tokens', 0),
        total_tokens=usage.get('total_tokens', 0),
        api_calls=usage.get('api_calls', 0),
        session='$logfile',
        status='$status',
    )
" >> logs/daemon.log 2>&1 || true

  # Log status (daily digest sent at end of research window instead)
  echo "Session $status: $logfile" >> logs/daemon.log
}

# ---- Main loop ----

echo "Daemon started. Research every ${SESSION_INTERVAL}s, trades every ${TRADE_INTERVAL}s, health every ${HEALTH_INTERVAL}s." | tee -a logs/daemon.log

while true; do
  current=$(now)

  # Trade loop — every 2 min (fast, lightweight)
  if (( current - last_trade >= TRADE_INTERVAL )); then
    run_trade_loop
    last_trade=$(now)
  fi

  # Health check — every 10 min
  if (( current - last_health >= HEALTH_INTERVAL )); then
    run_health_check
    last_health=$(now)
  fi

  # Research session — only if there's actual work to do (daily cap still enforced)
  if (( current - last_session >= SESSION_INTERVAL )); then
    if python3 should_run.py >> logs/daemon.log 2>&1; then
      run_session &
    else
      # Heartbeat proves the daemon is alive to the watchdog even when skipping
      # (peak hours, no work, or daily cap) — otherwise health_check.py alerts
      # after 2h of skips.
      echo "=== Heartbeat $(date) ===" >> logs/daemon.log
      echo "$(date): No actionable work — skipping session." >> logs/daemon.log
    fi
    last_session=$(now)
  fi

  # Dashboard export — every hour
  if (( current - last_export >= EXPORT_INTERVAL )); then
    python3 dashboard/export.py >> logs/daemon.log 2>&1 || true
    last_export=$(now)
  fi

  # Daily digest — send once at 7 AM
  hour=$((10#$(date +%H)))
  if (( hour >= 7 && hour < 8 && digest_sent == 0 )); then
    echo "Sending daily digest..." >> logs/daemon.log
    python3 -c "from email_report import send_report; send_report()" >> logs/daemon.log 2>&1 || true
    digest_sent=1
  fi
  # Reset digest flag after the window
  if (( hour >= 8 )); then
    digest_sent=0
  fi

  sleep $TICK
done
