#!/bin/zsh

set -euo pipefail

show_usage() {
  cat <<'EOF'
用法:
  scripts/quick_commit.sh [提交说明] [--push] [--dry-run] [--allow-output]

示例:
  scripts/quick_commit.sh "feat: 支持 InfoQ 抓取配置"
  scripts/quick_commit.sh "docs: 补充开发规范" --push
  scripts/quick_commit.sh --dry-run

默认行为:
  - 自动在项目根目录执行
  - 执行 git add -A，但排除本地密钥、运行输出和构建产物
  - 如果没有传提交说明，自动生成 chore: quick commit YYYY-MM-DD HH:MM
  - 只有传 --push 才会推送远程

安全规则:
  - 默认不提交 .env*、runtime.local.json、output/、build/
  - 若暂存区已有上述敏感或生成物路径，脚本会停止
  - 如确实需要提交 output/build 路径，传 --allow-output
EOF
}

push_after_commit=false
dry_run=false
allow_output=false
message_parts=()

while (( $# > 0 )); do
  case "$1" in
    --push)
      push_after_commit=true
      ;;
    --dry-run)
      dry_run=true
      ;;
    --allow-output)
      allow_output=true
      ;;
    -h|--help)
      show_usage
      exit 0
      ;;
    --)
      shift
      while (( $# > 0 )); do
        message_parts+=("$1")
        shift
      done
      break
      ;;
    -*)
      echo "未知参数: $1" >&2
      show_usage >&2
      exit 2
      ;;
    *)
      message_parts+=("$1")
      ;;
  esac
  shift
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if (( ${#message_parts[@]} > 0 )); then
  commit_message="${(j: :)message_parts}"
else
  commit_message="chore: quick commit $(date '+%Y-%m-%d %H:%M')"
fi

has_worktree_changes() {
  [[ -n "$(git status --short --untracked-files=all)" ]]
}

blocked_path_pattern() {
  if [[ "$allow_output" == true ]]; then
    echo '(^|/)(\.env(\..*)?|runtime\.local\.json|.*\.pem|.*\.key)$'
  else
    echo '(^|/)(\.env(\..*)?|runtime\.local\.json|.*\.pem|.*\.key)$|(^|)(build|.*output)/'
  fi
}

ensure_no_blocked_staged_paths() {
  local pattern
  pattern="$(blocked_path_pattern)"
  local blocked
  blocked="$(git diff --cached --name-only | grep -E "$pattern" || true)"
  if [[ -n "$blocked" ]]; then
    echo "发现不应快速提交的暂存路径，已停止:" >&2
    echo "$blocked" >&2
    echo "" >&2
    echo "请手工确认后再提交；如确实要提交 output/build，可使用 --allow-output。" >&2
    exit 1
  fi
}

if ! has_worktree_changes; then
  echo "工作区没有可提交变更。"
  exit 0
fi

echo "项目目录: $repo_root"
echo "当前分支: $(git branch --show-current)"
echo "提交说明: $commit_message"
echo ""
echo "当前变更:"
git status --short --untracked-files=all

if [[ "$dry_run" == true ]]; then
  echo ""
  echo "dry-run 模式：未执行 git add / git commit / git push。"
  exit 0
fi

# 默认排除本地配置、运行输出和构建产物；这些内容通常不可复现或包含敏感信息。
git add -A -- \
  . \
  ':(exclude).env' \
  ':(exclude).env.*' \
  ':(exclude)**/.env' \
  ':(exclude)**/.env.*' \
  ':(exclude)**/runtime.local.json' \
  ':(exclude)build/**' \
  ':(exclude)**/output/**'

ensure_no_blocked_staged_paths

if git diff --cached --quiet; then
  echo "排除本地配置和输出目录后，没有可提交内容。"
  exit 0
fi

echo ""
echo "即将提交的文件:"
git diff --cached --name-status

git commit -m "$commit_message"

if [[ "$push_after_commit" == true ]]; then
  current_branch="$(git branch --show-current)"
  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    git push
  else
    git push -u origin "$current_branch"
  fi
fi
