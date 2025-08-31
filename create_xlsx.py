# create_xlsx.py
# Converts your completions.csv -> completions_<ids>.xlsx
# Columns:
#   Canvas User ID | Student Last Name | Student First Name | Student Submission Text | Grade | Upload?

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

import pandas as pd
import requests
from pandas import NA

# ───────── Secrets / URL ─────────

def _normalize_api_base(url: str) -> str:
    u = url.strip().rstrip("/")
    if u.endswith("/api/v1"):
        u = u[:-len("/api/v1")]
    return u + "/api/v1"

def _load_canvas_credentials() -> Tuple[str, str]:
    key_path = Path.home() / "canvas-secrets.key"
    lines = [ln.strip() for ln in key_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("canvas-secrets.key must have URL on line 1 and token on line 2")
    return _normalize_api_base(lines[0]), lines[1]

# ───────── Canvas helpers ─────────

def _get_all_pages(session: requests.Session, url: str, params: Optional[dict] = None) -> List[dict]:
    out: List[dict] = []
    first = True
    page_url: Optional[str] = url
    while page_url:
        r = session.get(page_url, params=params if first else None)
        first = False
        r.raise_for_status()
        payload = r.json()
        out.extend(payload if isinstance(payload, list) else [cast(dict, payload)])
        next_url: Optional[str] = None
        link = r.headers.get("Link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                i = part.find("<") + 1
                j = part.find(">")
                if i > 0 and j > i:
                    next_url = part[i:j]
                break
        page_url = next_url
    return out

def _split_sortable_name(sortable_name: Optional[str]) -> Tuple[str, str]:
    if not sortable_name:
        return "", ""
    if "," in sortable_name:
        last, first = [p.strip() for p in sortable_name.split(",", 1)]
        return last, first
    parts = sortable_name.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])

def _normalize_name_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s*-\s*\d+\s*$", "", s)
    s = re.sub(r"\s+", " ", s)
    s = s.replace(".", "")
    return s

def _build_roster_maps(api_base: str, token: str, course_id: int) -> Tuple[Dict[int, Tuple[str, str]], Dict[str, int]]:
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    url = f"{api_base}/courses/{course_id}/users"
    params = {"per_page": 100, "enrollment_type[]": "student"}
    users = _get_all_pages(session, url, params=params)

    id_to_names: Dict[int, Tuple[str, str]] = {}
    namekey_to_id: Dict[str, int] = {}

    for u in users:
        try:
            uid = int(u.get("id"))
        except Exception:
            continue
        last, first = _split_sortable_name(cast(Optional[str], u.get("sortable_name")) or cast(Optional[str], u.get("name")))
        id_to_names[uid] = (last, first)
        if last or first:
            k1 = _normalize_name_key(f"{last}, {first}".strip(", ").strip())
            k2 = _normalize_name_key(f"{first} {last}".strip())
            if k1: namekey_to_id[k1] = uid
            if k2: namekey_to_id[k2] = uid

    return id_to_names, namekey_to_id

# ───────── CSV parsing helpers ─────────

_URL_AND_TEXT = re.compile(r"^\s*(https?://\S+)\s*(.*)\s*$", re.DOTALL)
_IDS_FROM_URL = re.compile(r"/courses/(\d+)/assignments/(\d+)/submissions/(\d+)")
_ID_AT_END   = re.compile(r"(\d{4,})\s*$")

def _strip_url_prefix(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    m = _URL_AND_TEXT.match(s)
    return (m.group(2) if m else s).strip()

def _extract_ids_from_col_b(s: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if not isinstance(s, str):
        return None, None, None
    m = _IDS_FROM_URL.search(s)
    if not m:
        return None, None, None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except Exception:
        return None, None, None

def _extract_user_id_from_col_a(s: Any) -> Optional[int]:
    if not isinstance(s, str):
        return None
    m = _ID_AT_END.search(s.strip())
    return int(m.group(1)) if m else None

def _extract_course_and_assignment_from_df(df: pd.DataFrame) -> Tuple[Optional[int], Optional[int]]:
    course_id: Optional[int] = None
    assignment_id: Optional[int] = None
    if 1 in df.columns:
        for val_any in df[1].dropna().tolist():
            c_id, a_id, _ = _extract_ids_from_col_b(val_any)
            if c_id is not None:
                course_id = c_id
            if a_id is not None:
                assignment_id = a_id
            if course_id is not None and assignment_id is not None:
                break
    # env fallbacks
    if course_id is None:
        env = os.getenv("CANVAS_DEFAULT_COURSE_ID")
        if env:
            try: course_id = int(env)
            except Exception: pass
    if assignment_id is None:
        env = os.getenv("CANVAS_DEFAULT_ASSIGNMENT_ID")
        if env:
            try: assignment_id = int(env)
            except Exception: pass
    return course_id, assignment_id

def _name_from_col_a(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s*-\s*\d+\s*$", "", s.strip())

# ───────── Main API ─────────

def create_xlsx(filename: str) -> None:
    """
    Read `filename` (completions.csv) and write an XLSX with:
      Canvas User ID | Student Last Name | Student First Name | Student Submission Text | Grade | Upload?
    Filename will include course/assignment IDs when available:
      completions_course{COURSE}_assign{ASSIGN}.xlsx
    """
    csv_path = Path(filename)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path, header=None)
    for c in range(3):
        if c not in df.columns:
            df[c] = ""

    # Detect course & assignment IDs (for roster + filename)
    course_id, assignment_id = _extract_course_and_assignment_from_df(df)

    id_to_names: Dict[int, Tuple[str, str]] = {}
    namekey_to_id: Dict[str, int] = {}
    if course_id is not None:
        api_base, token = _load_canvas_credentials()
        id_to_names, namekey_to_id = _build_roster_maps(api_base, token, int(course_id))

    user_ids: List[Optional[int]] = []
    last_names: List[str] = []
    first_names: List[str] = []
    texts: List[str] = []
    grades: List[str] = []
    upload_flags: List[str] = []

    for _, row in df.iterrows():
        col_a: Any = row[0] if 0 in df.columns else ""
        col_b: Any = row[1] if 1 in df.columns else ""
        col_c: Any = row[2] if 2 in df.columns else ""

        # Resolve user_id: A tail → B URL → name match
        uid: Optional[int] = _extract_user_id_from_col_a(col_a)
        if uid is None:
            _, _, uid = _extract_ids_from_col_b(col_b)
        if uid is None and namekey_to_id:
            k = _normalize_name_key(_name_from_col_a(col_a))
            uid = namekey_to_id.get(k)

        if uid is not None and uid in id_to_names:
            last, first = id_to_names[uid]
        else:
            last = first = ""

        text = _strip_url_prefix(col_b)

        user_ids.append(uid)
        last_names.append(last)
        first_names.append(first)
        texts.append(text)
        grades.append(str(col_c))
        upload_flags.append("yes")

   # Convert Optional[int] → nullable Int64 with pd.NA (not None)
    uid_arr = pd.array([uid if isinstance(uid, int) else NA for uid in user_ids], dtype="Int64")

    out = pd.DataFrame({
        "Canvas User ID": uid_arr,
        "Student Last Name": last_names,
        "Student First Name": first_names,
        "Student Submission Text": texts,
        "Grade": grades,
        "Upload?": upload_flags,
    })

    # Build filename with IDs
    base = csv_path.with_suffix("").name
    suffix_parts: List[str] = []
    if course_id is not None:
        suffix_parts.append(f"course{course_id}")
    if assignment_id is not None:
        suffix_parts.append(f"assign{assignment_id}")
    suffix = ("_" + "_".join(suffix_parts)) if suffix_parts else ""
    xlsx_path = csv_path.with_name(f"{base}{suffix}.xlsx")

    # Write Excel
    with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as writer:
        out.to_excel(writer, index=False, sheet_name="Submissions")
        ws_any: Any = writer.sheets["Submissions"]
        wb_any: Any = writer.book
        wrap = wb_any.add_format({"text_wrap": True, "valign": "top"})
        ws_any.set_column("A:A", 14)         # Canvas User ID
        ws_any.set_column("B:C", 20)         # Last, First
        ws_any.set_column("D:D", 100, wrap)  # Submission text
        ws_any.set_column("E:E", 24)         # Grade text
        ws_any.set_column("F:F", 10)         # Upload?
        for r in range(1, len(out) + 1):
            ws_any.set_row(r, 24)
