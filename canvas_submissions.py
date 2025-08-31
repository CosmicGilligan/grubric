# canvas_submissions_flat.py
from __future__ import annotations
import os, re, shutil, html
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _normalize_api_base(raw_base: str) -> str:
    base = raw_base.strip().rstrip("/")
    if base.endswith("/api/v1"):
        base = base[:-len("/api/v1")]
    return base + "/api/v1"

def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\s,.'\"`]+", "", s)   # strip spaces/punct to get blancoerick
    s = re.sub(r"[^a-z0-9_-]", "_", s)
    return s

def _student_stem(user: Dict[str, Any]) -> str:
    # Prefer sortable_name: "Last, First"
    sortable = user.get("sortable_name") or ""
    if "," in sortable:
        last, first = [p.strip() for p in sortable.split(",", 1)]
        return _slug(last + first)
    # Fallback: use full name split
    name = user.get("name") or ""
    parts = name.split()
    if len(parts) >= 2:
        return _slug(parts[-1] + parts[0])
    return _slug(name or f"student{user.get('id','unknown')}")

def _wrap_as_html(title: str, inner_html: str) -> str:
    # If it already looks like HTML, keep as-is inside a body.
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body>
{inner_html}
</body></html>
"""

def _parse_next_link(r: requests.Response) -> Optional[str]:
    link = r.headers.get("Link", "")
    for part in link.split(","):
        if 'rel="next"' in part:
            start = part.find("<") + 1
            end = part.find(">")
            if start > 0 and end > start:
                return part[start:end]
    return None

def _get_with_pagination(session: requests.Session, url: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    first = True
    page_url: Optional[str] = url
    while page_url:
        r = session.get(page_url, params=params if first else None)
        first = False
        r.raise_for_status()
        payload = r.json()
        if isinstance(payload, list):
            out.extend(payload)
        else:
            # some endpoints return an object wrapper
            out.append(payload)
        page_url = _parse_next_link(r)
    return out

def download_submissions_flat(
    canvas_base_url: str,
    token: str,
    course_id: int,
    assignment_id: int,
    dest_dir: str = "./submissions",
    clean_dest: bool = True
) -> Tuple[int, int]:
    """
    Downloads submissions to a FLAT folder like:
      ./submissions/blancoerick_9051950_text.html
      ./submissions/blancoerick_9051950_essay.docx
      ./submissions/blancoerick_9051950_url.txt
    Returns: (num_students_processed, num_files_saved)
    """
    api_base = _normalize_api_base(canvas_base_url)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if clean_dest:
        # remove existing files only (not the folder)
        for p in dest.glob("*"):
            if p.is_file():
                try: p.unlink()
                except Exception: pass

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    # include user + attachments
    url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions"
    params = {"per_page": 100, "include[]": ["user", "attachments"]}
    submissions = _get_with_pagination(session, url, params)

    students = 0
    files_saved = 0

    for sub in submissions:
        state = sub.get("workflow_state")
        if state not in ("submitted", "graded"):   # skip unsubmitted
            continue

        user = sub.get("user") or {}
        user_id = user.get("id")
        if user_id is None:
            # can't construct exact filename without id; skip safely
            continue
        stem = f"{_student_stem(user)}_{user_id}"

        # 1) online text entry (HTML body)
        body = sub.get("body")
        if isinstance(body, str) and body.strip():
            # Canvas 'body' is HTML; wrap in minimal page for consistency
            title = sub.get("preview_url") or f"Assignment {assignment_id}"
            html_text = _wrap_as_html(str(title), body)
            out_path = dest / f"{stem}_text.html"
            out_path.write_text(html_text, encoding="utf-8", errors="ignore")
            files_saved += 1

        # 2) online URL
        online_url = sub.get("url") or sub.get("online_url") or ""
        if online_url:
            (dest / f"{stem}_url.txt").write_text(online_url, encoding="utf-8", errors="ignore")
            files_saved += 1

        # 3) file attachments
        for att in sub.get("attachments") or []:
            att_url = att.get("url") or ""
            att_name = att.get("filename") or att.get("display_name") or f"file_{att.get('id','unknown')}"
            safe_name = _slug(att_name)
            out_path = dest / f"{stem}_{safe_name}"
            if att_url:
                with session.get(att_url, stream=True) as r:
                    r.raise_for_status()
                    with open(out_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                files_saved += 1
            else:
                # No direct URL (e.g., cloud LTI) — record metadata
                out_path.with_suffix(".json").write_text(str(att), encoding="utf-8", errors="ignore")
                files_saved += 1

        students += 1

    return students, files_saved
