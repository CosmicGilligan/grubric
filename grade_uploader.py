# grade_uploader.py
# Upload grades/comments back to Canvas from:
#  - the in-memory entryList (current session),
#  - a saved XLSX produced by create_xlsx.py,
#  - or the original CSV (A=name+id, B=url+text, C=grade).
#
# Public functions:
#   upload_all_from_entrylist(api_base, token, course_id, assignment_id, entry_list)
#   upload_all_from_xlsx(api_base, token, course_id, assignment_id, xlsx_path, respect_upload_flag=True)
#   upload_all_from_csv(api_base, token, course_id, assignment_id, csv_path)

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import re
import requests
import pandas as pd

__all__ = [
    "upload_all_from_entrylist",
    "upload_all_from_xlsx",
    "upload_all_from_csv",
    # helpers (exported in case you want to reuse):
    "parse_score_and_comment",
    "extract_user_id_from_label",
    "upload_grade_and_comment",
]

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

# Parse leading "score/max" at start of feedback, e.g. "8.5/12.0 (C) - comment..."
_SCORE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)")
# Grab a Canvas user_id (4+ digits) from a label string
_UID_ANYWHERE = re.compile(r"(\d{4,})")
# For CSV fallback, pull id from Column B URL ".../submissions/<id>"
_ID_FROM_URL = re.compile(r"/submissions/(\d+)")

def parse_score_and_comment(feedback: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    Extract (score, max_points, comment_text) from a feedback string like:
        "8.5/12.0 (C) - Good job overall..."
    If no score is found, returns (None, None, <whole string or suffix after ' - '>).
    """
    if not isinstance(feedback, str):
        return None, None, ""
    score: Optional[float] = None
    max_pts: Optional[float] = None

    m = _SCORE_RE.match(feedback)
    if m:
        try:
            score = float(m.group(1))
            max_pts = float(m.group(2))
        except (ValueError, TypeError):
            score = None
            max_pts = None

    # Text after the first " - " (if present) is the comment
    dash_idx = feedback.find(" - ")
    comment = feedback[dash_idx + 3:] if dash_idx != -1 else feedback
    return score, max_pts, comment.strip()

def extract_user_id_from_label(label: str) -> Optional[int]:
    """
    Pull a Canvas user_id from a label, e.g.:
      "Smith, Jane - 9051950"
      "blancoerick_9051950_text.html"
      "Doe, John (1234567)"
    """
    if not isinstance(label, str):
        return None
    m = _UID_ANYWHERE.search(label)
    try:
        return int(m.group(1)) if m else None
    except Exception:
        return None

def upload_grade_and_comment(
    api_base: str,
    token: str,
    course_id: int,
    assignment_id: int,
    user_id: int,
    score: Optional[float],
    comment: str,
) -> Dict[str, Any]:
    """
    PUT /courses/:course_id/assignments/:assignment_id/submissions/:user_id
      - submission[posted_grade]: numeric points (string)
      - comment[text_comment]: free text comment (string)
    """
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    data: Dict[str, Any] = {}
    if score is not None:
        data["submission[posted_grade]"] = str(score)
    if comment:
        data["comment[text_comment]"] = comment[:10000]  # safety limit
    r = requests.put(url, headers=headers, data=data)
    r.raise_for_status()
    return r.json()

def _is_truthy(val: Any) -> bool:
    """Interpret values from the XLSX 'Upload?' column."""
    if isinstance(val, str):
        v = val.strip().lower()
        return v in ("y", "yes", "true", "1")
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, bool):
        return val
    # default truthy if present
    return True

# ─────────────────────────────────────────────────────────
# Uploaders
# ─────────────────────────────────────────────────────────

def upload_all_from_entrylist(
    api_base: str,
    token: str,
    course_id: int,
    assignment_id: int,
    entry_list: List[List[str]],
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """
    entry_list rows look like: [student_label, submission_text, feedback]
    Returns: (success_count, fail_count, failures[(label, reason)])
    """
    successes = 0
    failures: List[Tuple[str, str]] = []
    for row in entry_list:
        label = "<unknown>"  # ensure bound for except
        try:
            label = row[0] if len(row) > 0 else "<unknown>"
            feedback = row[2] if len(row) > 2 else ""
            user_id = extract_user_id_from_label(label)
            if user_id is None:
                failures.append((label, "No Canvas user_id found in label"))
                continue
            score, _, comment = parse_score_and_comment(feedback)
            upload_grade_and_comment(api_base, token, int(course_id), int(assignment_id), user_id, score, comment)
            successes += 1
        except Exception as e:
            failures.append((label, str(e)))
    return successes, len(failures), failures

def upload_all_from_xlsx(
    api_base: str,
    token: str,
    course_id: int,
    assignment_id: int,
    xlsx_path: str,
    respect_upload_flag: bool = True,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """
    Expects an XLSX with columns produced by create_xlsx.py:
      Canvas User ID | Student Last Name | Student First Name | Student Submission Text | Grade | Upload?
    """
    df = pd.read_excel(xlsx_path, sheet_name="Submissions")
    required = ["Canvas User ID", "Grade"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"XLSX is missing required column: {col}")

    successes = 0
    failures: List[Tuple[str, str]] = []
    for _, row in df.iterrows():
        label_for_log = f"{row.get('Student Last Name','')}, {row.get('Student First Name','')}".strip()
        try:
            if respect_upload_flag and "Upload?" in df.columns:
                flag = row.get("Upload?", "yes")
                if not _is_truthy(flag):
                    continue

            uid_val = row.get("Canvas User ID", None)
            if pd.isna(uid_val):
                failures.append((label_for_log or "(missing name)", "Canvas User ID is blank"))
                continue
            user_id = int(uid_val)

            feedback_val = row.get("Grade", "")
            feedback = "" if pd.isna(feedback_val) else str(feedback_val)
            score, _, comment = parse_score_and_comment(feedback)

            upload_grade_and_comment(api_base, token, int(course_id), int(assignment_id), user_id, score, comment)
            successes += 1
        except Exception as e:
            failures.append((label_for_log or f"uid={row.get('Canvas User ID','?')}", str(e)))
    return successes, len(failures), failures

def upload_all_from_csv(
    api_base: str,
    token: str,
    course_id: int,
    assignment_id: int,
    csv_path: str,
) -> Tuple[int, int, List[Tuple[str, str]]]:
    """
    CSV format: col A = name+id (may include a trailing id), col B = url+text, col C = grade.
    Derive user_id from col A; if not found, fallback to id in col B URL (.../submissions/<id>).
    """
    df = pd.read_csv(csv_path, header=None)
    successes = 0
    failures: List[Tuple[str, str]] = []

    for _, row in df.iterrows():
        label = "(unknown)"  # ensure bound
        try:
            label = str(row[0]) if 0 in df.columns else "(unknown)"
            b = str(row[1]) if 1 in df.columns else ""
            feedback = str(row[2]) if 2 in df.columns else ""

            # Try label first
            m = _UID_ANYWHERE.search(label)
            user_id: Optional[int] = int(m.group(1)) if m else None
            # Fallback to URL
            if user_id is None:
                m2 = _ID_FROM_URL.search(b)
                if m2:
                    user_id = int(m2.group(1))
            if user_id is None:
                failures.append((label, "No Canvas user_id found"))
                continue

            score, _, comment = parse_score_and_comment(feedback)
            upload_grade_and_comment(api_base, token, int(course_id), int(assignment_id), user_id, score, comment)
            successes += 1
        except Exception as e:
            failures.append((label, str(e)))
    return successes, len(failures), failures
