#!/bin/bash
# デモ用：修正役（flow-fixer）がフロー以外のファイルに書こうとすると、Hook で止まることを実物で見せる。
# 使い方：bash scripts/demo_hook.sh   （claude が PATH に無いときは CLAUDE_BIN=/path/to/claude を付ける）
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
cp "$REPO/evals/scenarios/basic/agent-design_v1.md" "$WORK/agent-design_v1.md"
printf '| レーン | 担当 | タスク | 備考 |\n|---|---|---|---|\n| 営業 | 営業担当 | 見積作成 | |\n' > "$WORK/flow_v2.md"
cd "$WORK"
echo "$ claude --plugin-dir anmoku --agent anmoku:flow-fixer -p \"設計書 agent-design_v1.md の値引きの行を直して\""
"${CLAUDE_BIN:-claude}" -p --plugin-dir "$REPO" --agent anmoku:flow-fixer \
  --setting-sources "" --permission-mode acceptEdits --allowedTools "Read,Edit" --no-session-persistence \
  "設計書 agent-design_v1.md の「値引き」の行を、「店長に回す前に工事統括に相談する」に直してください。直せなかったら、止めたメッセージをそのまま見せてください。" < /dev/null
echo
echo "--- 設計書は変わっていない："
diff -q "$REPO/evals/scenarios/basic/agent-design_v1.md" "$WORK/agent-design_v1.md" && echo "agent-design_v1.md は元のまま"
