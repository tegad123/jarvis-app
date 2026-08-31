#!/usr/bin/env bash
set -uo pipefail

usage() {
  echo "Usage: tools/preflight.sh <branch-name>" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

branch=$1
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "RED: not inside a git repository" >&2
  exit 1
}
cd "$repo_root"

failures=()
secret_pattern='sk''-ant-'
skill=".cursor/skills/verify-jarvis/SKILL.md"

echo "1. Branch commits"
if ! git show-ref --verify --quiet "refs/heads/$branch"; then
  echo "  FAIL: branch '$branch' does not exist"
  exit 1
fi

mapfile -t commits < <(git rev-list --reverse "main..$branch")
if [[ ${#commits[@]} -eq 0 ]]; then
  failures+=("branch '$branch' has no commits not on main")
  echo "  FAIL: ${failures[-1]}"
else
  printf '  %s\n' "${commits[@]}"
fi

mapfile -t changed_files < <(git diff --name-only "main..$branch" --)

echo "2. Forbidden paths and content"
forbidden_failures=()
for file in "${changed_files[@]}"; do
  if [[ "$file" =~ (^|/)\.env[^/]*(/|$) ]]; then
    forbidden_failures+=("forbidden .env path: $file")
  fi
  if [[ "$file" =~ (^|/)root/\.hermes(/|$) ]] ||
     [[ "$file" =~ (^|/)opt/testco/hermes(/|$) ]]; then
    forbidden_failures+=("forbidden runtime path: $file")
  fi
  if [[ "$file" =~ (^|/)(etc/systemd/system|systemd)(/|$) ]] ||
     [[ "$file" =~ \.(service|socket|timer|target|path|mount|automount|slice)$ ]]; then
    forbidden_failures+=("forbidden systemd unit path: $file")
  fi
  if [[ "$file" == *"$secret_pattern"* ]]; then
    forbidden_failures+=("forbidden $secret_pattern pattern in path: $file")
  fi
done

mapfile -t secret_files < <(git diff --name-only -G"$secret_pattern" "main..$branch" --)
for file in "${secret_files[@]}"; do
  forbidden_failures+=("forbidden $secret_pattern pattern in diff: $file")
done

if [[ ${#forbidden_failures[@]} -eq 0 ]]; then
  echo "  PASS: no forbidden paths or $secret_pattern patterns"
else
  for reason in "${forbidden_failures[@]}"; do
    failures+=("$reason")
    echo "  FAIL: $reason"
  done
fi

echo "3. Changed files"
if [[ ${#changed_files[@]} -eq 0 ]]; then
  echo "  (none)"
else
  printf '  %s\n' "${changed_files[@]}"
fi

code_files=()
app_changed=false
for file in "${changed_files[@]}"; do
  if [[ "$file" == app/* || "$file" == frontend/* ]]; then
    code_files+=("$file")
  fi
  if [[ "$file" == app/* ]]; then
    app_changed=true
  fi
done

if [[ ${#code_files[@]} -gt 0 ]]; then
  echo "  Applicable verification from $skill:"
  if [[ ! -f "$skill" ]]; then
    failures+=("feature map unparseable — gate cannot name verification")
    echo "  FAIL: feature map unparseable — gate cannot name verification ($skill missing)"
  else
    map_out=$(python3 - "$skill" "${code_files[@]}" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
files = sys.argv[2:]
rows = []
in_table = False
for line in text.splitlines():
    if line.startswith("| Surface |"):
        in_table = True
        continue
    if not in_table:
        continue
    if not line.startswith("|"):
        break
    if re.match(r"^\|[-:| ]+\|$", line):
        continue
    columns = [part.strip() for part in line.strip().strip("|").split("|")]
    if len(columns) < 3:
        continue
    surface = re.sub(r"[*`]", "", columns[0]).strip()
    if not surface:
        continue
    implementations = columns[1]
    scripts = [
        token for token in re.findall(r"`([^`]+)`", columns[2])
        if re.search(r"(^|/)(verify[^/` ]+|roles)\.(py|mjs)$", token)
        or token.endswith("roles.py")
    ]
    rows.append((surface, implementations, scripts))

if not rows:
    print("FAIL: feature map unparseable — gate cannot name verification")
    sys.exit(2)

for file in files:
    matches = []
    for surface, implementations, scripts in rows:
        tokens = re.findall(r"`([^`]+)`", implementations)
        direct = any(
            token == file
            or token.endswith("/" + Path(file).name)
            or ("/" not in token and Path(file).name == token)
            for token in tokens
        )
        if direct:
            matches.append((surface, scripts))
    if not matches:
        matches = [(surface, scripts) for surface, _, scripts in rows]
    for surface, scripts in matches:
        names = ", ".join(scripts) if scripts else "(feature map names no script)"
        print(f"    {file} -> {surface}: {names}")
PY
    )
    map_rc=$?
    if [[ -n "$map_out" ]]; then
      printf '%s\n' "$map_out"
    fi
    if [[ $map_rc -ne 0 ]]; then
      failures+=("feature map unparseable — gate cannot name verification")
      if [[ "$map_out" != *"feature map unparseable"* ]]; then
        echo "  FAIL: feature map unparseable — gate cannot name verification"
      fi
    fi
  fi
fi

echo "4. Ledger requirement"
if "$app_changed"; then
  echo "  app/ changed: merge requires a before/after ledger sandwich."
  echo "  WAL-inclusive snapshot command from the skill:"
  found_header=false
  command_lines=()
  printing=false
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "# Never install sqlite3. WAL-inclusive snapshot via the venv:" ]]; then
      found_header=true
      printing=true
      continue
    fi
    if "$printing"; then
      [[ "$line" == '```' ]] && break
      command_lines+=("$line")
    fi
  done < "$skill"
  if ! "$found_header"; then
    failures+=("snapshot command header not found in $skill")
    echo "  FAIL: snapshot command header not found in $skill"
  elif [[ ${#command_lines[@]} -eq 0 ]]; then
    failures+=("snapshot command extraction found the header but zero command lines")
    echo "  FAIL: snapshot command extraction found the header but zero command lines"
  else
    for line in "${command_lines[@]}"; do
      printf '    %s\n' "$line"
    done
  fi
else
  echo "  PASS: no app/ changes"
fi

echo "5. Dependency policy"
dependency_changes=()
for file in "${changed_files[@]}"; do
  case "${file##*/}" in
    requirements.txt|package.json) dependency_changes+=("$file") ;;
  esac
done
if [[ ${#dependency_changes[@]} -eq 0 ]]; then
  echo "  PASS: no unauthorized dependency manifest changes"
elif [[ "${PREFLIGHT_ALLOW_DEPS:-}" == "1" ]]; then
  echo "  PASS: dependency manifest changes allowed by PREFLIGHT_ALLOW_DEPS=1:"
  printf '    %s\n' "${dependency_changes[@]}"
else
  for file in "${dependency_changes[@]}"; do
    failures+=("dependency manifest changed: $file (set PREFLIGHT_ALLOW_DEPS=1 to allow)")
    echo "  FAIL: ${failures[-1]}"
  done
fi

if [[ ${#failures[@]} -gt 0 ]]; then
  echo
  echo "RED: branch is not mergeable"
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi

echo
echo "GREEN: branch is mergeable"
