#!/usr/bin/env bash
# Create private GitHub repo and push (requires: gh auth login).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! gh auth status >/dev/null 2>&1; then
  echo "Not logged in. Run once:"
  echo "  gh auth login -h github.com -p ssh -w"
  exit 1
fi

OWNER=$(gh api user --jq .login)
NAME="${1:-vesper-forge}"
URL="https://github.com/${OWNER}/${NAME}"

if gh repo view "${OWNER}/${NAME}" >/dev/null 2>&1; then
  echo "Repo exists: ${URL}"
  git remote remove origin 2>/dev/null || true
  git remote add origin "git@github.com:${OWNER}/${NAME}.git"
  git push -u origin main
else
  gh repo create "${NAME}" --private --source=. --remote=origin --push \
    --description "Local Grok Build session browser with Obsidian sync"
fi

echo ""
echo "Private repo: ${URL}"
echo "Invite collaborator (GitHub UI or):"
echo "  gh api -X PUT repos/${OWNER}/${NAME}/collaborators/GITHUB_USERNAME -f permission=push"
echo "Then report URL to Lilith for review."
