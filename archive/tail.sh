#!/bin/bash
cd "$(dirname "$0")"
LATEST=$(ls -t logs/*.log 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
  echo "No logs found"
  exit 1
fi
echo "Tailing: $LATEST"
echo "---"
tail -f "$LATEST" | python3 -u -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        t = d.get('type')
        if t == 'assistant':
            for c in d['message']['content']:
                if c['type'] == 'text' and c['text'].strip():
                    print(c['text'].strip())
                elif c['type'] == 'tool_use':
                    print(f'[tool: {c[\"name\"]}]')
        elif t == 'result':
            print(f'--- Done: {d[\"num_turns\"]} turns, \${d[\"total_cost_usd\"]:.2f} ---')
    except:
        if line.startswith('==='):
            print(line.strip())
"
