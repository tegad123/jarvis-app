#!/usr/bin/env bash
# Type-checks ios/JarvisApp/JarvisApp/**/*.swift on Linux against Shim/.
#
#   ios/reference/typecheck/check.sh
#
# The sources are copied into Generated/ with two edits a Linux toolchain
# needs: each `#Preview("name") {` becomes a plain call the shim declares
# (the macro itself ships with Xcode), and `@main` is dropped (a library
# target has no entry point). Nothing else is changed.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
app="$here/../../JarvisApp/JarvisApp"
out="$here/Generated"

rm -rf "$out"
mkdir -p "$out"

count=0
while IFS= read -r -d '' file; do
  name="$(echo "${file#"$app"/}" | tr '/' '_')"
  awk '
    /^#Preview\(/ {
      n += 1
      sub(/^#Preview\(/, "@MainActor private let _preview" n ": Void = _Preview(")
    }
    /^@main$/ { next }
    { print }
  ' "$file" > "$out/$name"
  count=$((count + 1))
done < <(find "$app" -name '*.swift' -print0 | sort -z)

echo "Type-checking $count files from ${app#"$here"/}"
cd "$here"
swift build --target JarvisAppCheck "$@"
