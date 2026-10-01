#!/usr/bin/env bash
# Creates a private GitHub repo under boidra-labs, pushes initial code,
# builds + pushes the first Docker image, and sets the GHCR package to private.
#
# Usage: bash scripts/create-github-repo.sh
# Run from the agent's root directory (where manifest.yml lives).
#
# Prerequisites:
#   - gh CLI installed and authenticated (gh auth login)
#   - docker installed and logged in to ghcr.io
#   - git installed

set -euo pipefail

# ---------------------------------------------------------------------------
# Derive agent name from the directory name
# ---------------------------------------------------------------------------
AGENT_NAME="$(basename "$(pwd)")"
ORG="boidra-labs"
IMAGE="ghcr.io/${ORG}/${AGENT_NAME}"
DESCRIPTION="Boidra Labs A2A agent: ${AGENT_NAME}"

echo "==> Agent: ${AGENT_NAME}"
echo "==> Org:   ${ORG}"
echo "==> Image: ${IMAGE}"
echo ""

# ---------------------------------------------------------------------------
# 1. Create private GitHub repo
# ---------------------------------------------------------------------------
echo "==> Creating private GitHub repo ${ORG}/${AGENT_NAME}..."
if gh repo view "${ORG}/${AGENT_NAME}" &>/dev/null; then
    echo "    Repo already exists — skipping creation."
else
    gh repo create "${ORG}/${AGENT_NAME}" \
        --private \
        --description "${DESCRIPTION}" \
        --clone=false
    echo "    Created."
fi

# ---------------------------------------------------------------------------
# 2. Push initial commit
# ---------------------------------------------------------------------------
echo "==> Initialising git and pushing to main..."
if [ ! -d ".git" ]; then
    git init
    git add .
    git commit -m "initial scaffold: ${AGENT_NAME}"
fi

REMOTE_URL="https://github.com/${ORG}/${AGENT_NAME}.git"
if ! git remote get-url origin &>/dev/null; then
    git remote add origin "${REMOTE_URL}"
fi

git push -u origin main
echo "    Pushed."

# ---------------------------------------------------------------------------
# 3. Build and push the first Docker image (creates the GHCR package)
# ---------------------------------------------------------------------------
echo "==> Building Docker image ${IMAGE}:0.1.0..."
docker build -t "${IMAGE}:0.1.0" .

echo "==> Pushing ${IMAGE}:0.1.0 to GHCR..."
docker push "${IMAGE}:0.1.0"
echo "    Pushed."

# ---------------------------------------------------------------------------
# 4. Set GHCR package visibility to private
#    (packages default to public on first push if created via docker push)
# ---------------------------------------------------------------------------
echo "==> Setting GHCR package visibility to private..."
ENCODED_NAME="${AGENT_NAME/\//%2F}"   # encode slashes if any
gh api \
    --method PATCH \
    -H "Accept: application/vnd.github+json" \
    "/orgs/${ORG}/packages/container/${ENCODED_NAME}/visibility" \
    -f visibility=private \
  && echo "    Package is now private." \
  || echo "    WARNING: Could not set package visibility — set it manually in GitHub UI."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "==> Done!"
echo ""
echo "Next steps:"
echo "  1. Update manifest.yml image tag if needed"
echo "  2. cf create-service aicore extended ${AGENT_NAME}-aicore"
echo "  3. cf create-service xsuaa application ${AGENT_NAME}-xsuaa -c xs-security.json"
echo "  4. cf push -f manifest.yml --docker-username <github-username>"
