#!/usr/bin/env bash
# Render joule-template/ -> joule/ by filling ${JOULE_*} placeholders.
# Values: environment variable (CI inputs / repo vars) > joule.env.
# Usage: scripts/render-joule.sh [values-file] [template-dir] [out-dir]
set -euo pipefail

VALUES_FILE="${1:-joule.env}"
TEMPLATE_DIR="${2:-joule-template}"
OUT_DIR="${3:-joule}"

VARS=(JOULE_AGENT_NAME JOULE_DISPLAY_NAME JOULE_DESTINATION JOULE_VERSION JOULE_DESCRIPTION
      JOULE_SCENARIO_NAME JOULE_SCENARIO_DESCRIPTION JOULE_RESPONSE_DESCRIPTION)

err() { if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::error::$*"; else echo "ERROR: $*"; fi >&2; exit 1; }

# 1. Load joule.env without overriding values already set in the environment
[ -f "$VALUES_FILE" ] || err "values file not found: $VALUES_FILE"
while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  key="${line%%=*}"; val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"
  if [ -z "${!key:-}" ]; then export "$key=$val"; fi
done < "$VALUES_FILE"

# 2. Version: auto -> git tag vX.Y.Z, else 1.0.0
if [ "${JOULE_VERSION:-auto}" = "auto" ]; then
  tag="${GITHUB_REF_TYPE:-}"; [ "$tag" = "tag" ] && tag="$GITHUB_REF_NAME" || tag="$(git describe --tags --exact-match 2>/dev/null || true)"
  [[ "$tag" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+) ]] && JOULE_VERSION="${BASH_REMATCH[1]}" || JOULE_VERSION="1.0.0"
fi
export JOULE_VERSION="${JOULE_VERSION#v}"

# 3. Validate inputs
for v in "${VARS[@]}"; do [ -n "${!v:-}" ] || err "$v is empty"; done
[[ "$JOULE_AGENT_NAME"    =~ ^[a-z][a-z0-9_]*$ ]]      || err "JOULE_AGENT_NAME must be snake_case: $JOULE_AGENT_NAME"
[[ "$JOULE_SCENARIO_NAME" =~ ^[a-z][a-z0-9_]*$ ]]      || err "JOULE_SCENARIO_NAME must be snake_case: $JOULE_SCENARIO_NAME"
[[ "$JOULE_DESTINATION"   =~ ^[A-Za-z0-9_-]+$ ]]       || err "JOULE_DESTINATION has invalid characters: $JOULE_DESTINATION"
[[ "$JOULE_VERSION"       =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || err "JOULE_VERSION must be X.Y.Z: $JOULE_VERSION"
for v in JOULE_DISPLAY_NAME JOULE_SCENARIO_DESCRIPTION JOULE_RESPONSE_DESCRIPTION; do
  [[ "${!v}" != *'"'* ]] || err "$v must not contain double quotes"
done

# 4. Render. Explicit variable list keeps Joule's own $capability_context / $target_result intact.
SHELL_FORMAT="$(printf '${%s} ' "${VARS[@]}")"
rm -rf "$OUT_DIR"
( cd "$TEMPLATE_DIR" && find . -type f ) | while read -r rel; do
  dest="${rel//__AGENT_NAME__/$JOULE_AGENT_NAME}"
  dest="${dest//__SCENARIO_NAME__/$JOULE_SCENARIO_NAME}"
  mkdir -p "$(dirname "$OUT_DIR/$dest")"
  envsubst "$SHELL_FORMAT" < "$TEMPLATE_DIR/$rel" > "$OUT_DIR/$dest"
  echo "  wrote $OUT_DIR/${dest#./}"
done

# 5. Validate output: no leftover placeholders, valid YAML
if grep -rnE '\$\{JOULE_[A-Z_]+\}|__[A-Z_]+__' "$OUT_DIR"; then err "unreplaced placeholders above"; fi
if command -v yq >/dev/null; then
  find "$OUT_DIR" -name '*.yaml' -exec yq e '.' {} + >/dev/null || err "generated YAML is invalid"
else
  echo "  (yq not installed - YAML syntax check skipped)"
fi

echo "Joule DTA rendered: agent=$JOULE_AGENT_NAME version=$JOULE_VERSION destination=$JOULE_DESTINATION scenario=$JOULE_SCENARIO_NAME"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  { echo "agent_name=$JOULE_AGENT_NAME"; echo "version=$JOULE_VERSION"; } >> "$GITHUB_OUTPUT"
fi
