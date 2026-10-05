#!/usr/bin/env bash
# Prune the grubric repo down to the code the app actually uses.
# Run from the root of the repo (the folder that contains rubric_grade_ui.py).
# Uses "git rm", so every deleted file stays in git history and can be restored.
set -euo pipefail

if [ ! -d .git ] || [ ! -f rubric_grade_ui.py ]; then
  echo "Run this from the root of the grubric repo (the folder with rubric_grade_ui.py)." >&2
  exit 1
fi

# 1. Stop tracking Python cache files, and ignore them from now on.
git rm -r -q --cached --ignore-unmatch __pycache__
grep -qx '__pycache__/' .gitignore 2>/dev/null || printf '__pycache__/\n*.pyc\n' >> .gitignore

# 2. Old snapshots, chat transcripts, and the old backup folder.
git rm -r -q --ignore-unmatch \
  grubric.zip claude1 claude1.txt new_config_ini.txt old_system_backup

# 3. The old question-based app and one-off scripts.
git rm -q --ignore-unmatch \
  grade_all_ui.py assignment_base.py assignment_factory.py assignment_manager.py \
  config_manager.py fixed_batch_grader_v2.py simple_individual_grader.py \
  batch_grading_system.py rubric_config_manager.py create_xlsx_FRESH.py migration_script.py

# 4. Three files that only exist because rubric_grade_ui.py used to "import grade_all".
#    Only delete them once that import is gone.
if grep -qE '^[[:space:]]*(import grade_all|from grade_all )' rubric_grade_ui.py; then
  echo
  echo "SKIPPED grade_all.py, claude_client.py, local_embeddings.py:"
  echo "rubric_grade_ui.py still imports grade_all. Install the updated rubric_grade_ui.py, then run this again."
else
  git rm -q --ignore-unmatch grade_all.py claude_client.py local_embeddings.py
fi

echo
git status --short | grep -v '^D  __pycache__/' || true
echo
echo "Done. Nothing is committed yet. Review with: git status"
echo "Commit with:  git commit -m 'Prune legacy files'"
echo "Undo everything before committing with:  git reset --hard HEAD"