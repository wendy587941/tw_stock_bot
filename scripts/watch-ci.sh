#!/usr/bin/env bash
# watch-ci.sh — 監看某個 commit 觸發的 GitHub Actions runs 直到全部完成
#
# 用法：
#   scripts/watch-ci.sh            # 監看目前 HEAD
#   scripts/watch-ci.sh <commit>   # 監看指定 commit（任何 git 可解析的 ref）
#   INTERVAL=30 MAX=40 scripts/watch-ci.sh   # 自訂輪詢間隔(秒)與最大次數
#
# 設計筆記：
#   - GitHub API 的 ?head_sha= 過濾需要「完整 40 碼 SHA」，用短 SHA 會比對不到（先前的雷）。
#     故這裡先 git rev-parse 解析成完整 SHA，再抓近 N 筆 runs 用 head_sha 精確比對。
#   - token 取自 git 已存憑證（Git Credential Manager），全程不 echo，避免外洩。
#   - 任一 run 失敗/取消/逾時 → 以 exit 1 收尾，方便接 CI 或 && 串接。
set -uo pipefail

COMMITISH="${1:-HEAD}"
SHA_FULL="$(git rev-parse "$COMMITISH")" || { echo "無法解析 commit：$COMMITISH" >&2; exit 2; }
SHA_SHORT="${SHA_FULL:0:7}"

# 從 origin remote 推導 owner/repo（支援 https 與 git@ 兩種格式）
REMOTE="$(git remote get-url origin)"
REPO="$(printf '%s' "$REMOTE" | sed -E 's#.*github\.com[:/]+##; s#\.git$##')"

# 取 GitHub token（不 echo）
TOKEN="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2)"
if [ -z "$TOKEN" ]; then echo "取不到 GitHub token（git credential）" >&2; exit 2; fi

INTERVAL="${INTERVAL:-20}"
MAX="${MAX:-60}"

echo "監看 $REPO @ $SHA_SHORT（每 ${INTERVAL}s，最多 ${MAX} 次）..."
for ((i = 1; i <= MAX; i++)); do
  RESP="$(curl -s -H "Authorization: Bearer $TOKEN" \
    "https://api.github.com/repos/$REPO/actions/runs?per_page=20")"

  OUT="$(printf '%s' "$RESP" | SHA="$SHA_FULL" python -c '
import os, sys, json
sha = os.environ["SHA"]
try:
    runs = [r for r in json.load(sys.stdin)["workflow_runs"] if r["head_sha"] == sha]
except Exception:
    print("ERR|API 回傳非預期（可能 token 無效或 rate limit）"); sys.exit()
if not runs:
    print("PENDING|尚無對應 run（可能還在排程中）")
else:
    parts = ["%s:%s/%s" % (r["name"], r["status"], r.get("conclusion") or "-") for r in runs]
    done = all(r["status"] == "completed" for r in runs)
    print(("DONE" if done else "WAIT") + "|" + " | ".join(parts))
')"

  STATE="${OUT%%|*}"
  MSG="${OUT#*|}"
  printf '[%02d] %s\n' "$i" "$MSG"

  case "$STATE" in
    DONE)
      if printf '%s' "$MSG" | grep -Eq 'failure|cancelled|timed_out'; then
        echo "❌ 有 run 未成功"; exit 1
      fi
      echo "✅ 全部 run 成功完成"; exit 0
      ;;
    ERR)
      exit 2
      ;;
  esac
  sleep "$INTERVAL"
done

echo "⏱ 逾時：$((INTERVAL * MAX))s 內未全部完成"
exit 2
