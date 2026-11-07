#!/usr/bin/env bash
# Replace the pickup address everywhere. Run from the repo root.
set -euo pipefail
OLD='Киренск, Балахня, ул. Колхозная, 32А'
NEW='Киренск, мкрн. Центральный, пер. Урицкого, 5'
FILES=$(grep -rlE 'Колхозн|32[АA]|102-79-95|79641027995' \
        --include='*.py' --include='*.html' --include='*.js' --include='*.md' \
        --include='*.json' --include='*.txt' --exclude-dir=.git --exclude-dir=.venv . || true)
echo "== files mentioning the old address/phone =="; echo "$FILES"
for f in $FILES; do
  sed -i "s/${OLD}/${NEW}/g" "$f"
  # plain-text variant with phone directly after the address
  sed -i -E "s/(Урицкого, 5), 📞 \+7 \(964\) 102-79-95/\1/g" "$f"
done
echo "== leftovers (review by hand: phone inside <a href=tel:...>, split strings) =="
grep -rnE 'Колхозн|32[АA]|102-79-95|79641027995' \
  --include='*.py' --include='*.html' --include='*.js' --include='*.md' \
  --include='*.json' --include='*.txt' --exclude-dir=.git --exclude-dir=.venv . || echo "none"
