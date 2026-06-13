#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || true

# Running?
PIDS=$(pgrep -f "researcher.sh")
if [ -n "$PIDS" ]; then
  echo "RUNNING (pid $PIDS)"
else
  echo "NOT RUNNING"
fi

# Latest log — extract activity from stream-json
LATEST=$(ls -t logs/*.log 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  echo ""
  echo "Latest: $LATEST ($(du -h "$LATEST" | cut -f1))"
  echo ""
  echo "Recent activity:"
  python3 -c "
import json
lines = open('$LATEST').readlines()
out = []
for line in lines:
    try:
        d = json.loads(line)
        if d.get('type') == 'assistant':
            for c in d['message']['content']:
                if c['type'] == 'text' and c['text'].strip():
                    out.append(c['text'].strip()[:200])
                elif c['type'] == 'tool_use':
                    out.append(f'[{c[\"name\"]}]')
        elif d.get('type') == 'result':
            out.append(f'--- Done: {d[\"num_turns\"]} turns, \${d[\"total_cost_usd\"]:.2f} ---')
    except:
        if line.startswith('==='):
            out.append(line.strip())
for line in out[-15:]:
    print(f'  {line}')
" 2>/dev/null
fi

# Research status
echo ""
python3 run.py --status 2>/dev/null
