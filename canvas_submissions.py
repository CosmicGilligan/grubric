# canvas_submissions_flat.py
from __future__ import annotations
import os, re, shutil, html
import requests
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
) -> Tuple[int, int, Dict[str, Dict[str, Any]]]:
    """
    Downloads submissions to a FLAT folder like:
      ./submissions/blancoerick_9051950_text.html
      ./submissions/blancoerick_9051950_essay.docx
      ./submissions/blancoerick_9051950_url.txt
    Returns: (num_students_processed, num_files_saved, student_metadata)
    
    student_metadata is a dict keyed by user_id with:
      {
        'name': 'Last, First',
        'current_score': float or None,
        'current_grade': str or None,
        'current_feedback': str,
        'submission_date': str or None
      }
    """
    api_base = _normalize_api_base(canvas_base_url)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if clean_dest:
        # Remove all existing files and subdirectories
        for p in dest.iterdir():
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)
            except Exception as e:
                logger.warning(f"Could not remove {p}: {e}")

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    # include user + attachments + submission comments
    url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions"
    params = {"per_page": 100, "include[]": ["user", "attachments", "submission_comments"]}
    submissions = _get_with_pagination(session, url, params)

    students = 0
    files_saved = 0
    student_metadata: Dict[str, Dict[str, Any]] = {}

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
        
        # Capture student metadata
        sortable_name = user.get("sortable_name") or user.get("name") or f"Student {user_id}"
        current_score = sub.get("score")  # Can be None if not graded yet
        current_grade = sub.get("grade")  # String grade (could be letter or points)
        submission_date = sub.get("submitted_at")
        
        # Capture existing feedback/comments
        current_feedback = ""
        submission_comments = sub.get("submission_comments") or []
        if submission_comments:
            # Get the most recent comment (they're usually in chronological order)
            # Filter out student comments, only get grader comments
            grader_comments = [c for c in submission_comments if c.get("author_id") != user_id]
            if grader_comments:
                latest_comment = grader_comments[-1]  # Most recent
                current_feedback = latest_comment.get("comment") or ""
        
        student_metadata[str(user_id)] = {
            'name': sortable_name,
            'current_score': float(current_score) if current_score is not None else None,
            'current_grade': str(current_grade) if current_grade else None,
            'current_feedback': current_feedback,
            'submission_date': submission_date
        }

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
                # No direct URL (e.g., cloud LTI) – record metadata
                out_path.with_suffix(".json").write_text(str(att), encoding="utf-8", errors="ignore")
                files_saved += 1

        students += 1

    return students, files_saved, student_metadata

# Add this function to your canvas_submissions.py file

def download_discussion_submissions(
    canvas_base_url: str,
    token: str,
    course_id: int,
    discussion_id: int,
    dest_dir: str = "./submissions",
    clean_dest: bool = True
) -> Tuple[int, int, Dict[str, Dict[str, Any]]]:
    """
    Downloads discussion submissions to a FLAT folder.
    Captures ALL student participation including:
    - Initial replies to the discussion prompt
    - Comments on other students' posts
    
    Returns: (num_students_processed, num_files_saved, student_metadata)
    """
    api_base = _normalize_api_base(canvas_base_url)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    
    if clean_dest:
        # Remove all existing files and subdirectories
        for p in dest.iterdir():
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)
            except Exception as e:
                logger.warning(f"Could not remove {p}: {e}")

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Get the discussion topic details to find assignment info
    topic_url = f"{api_base}/courses/{course_id}/discussion_topics/{discussion_id}"
    topic_response = session.get(topic_url)
    topic_response.raise_for_status()
    topic = topic_response.json()
    
    # Get assignment ID if discussion is graded
    assignment_id = topic.get("assignment_id")
    
    # If there's an assignment, get submission data for grades/feedback
    submission_data = {}
    if assignment_id:
        sub_url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions"
        sub_params = {"per_page": 100, "include[]": ["user", "submission_comments"]}
        submissions = _get_with_pagination(session, sub_url, sub_params)
        
        # Build a lookup dict by user_id
        for sub in submissions:
            user = sub.get("user") or {}
            user_id = user.get("id")
            if user_id:
                submission_data[user_id] = sub

    # Get ALL discussion entries (top-level posts)
    url = f"{api_base}/courses/{course_id}/discussion_topics/{discussion_id}/entries"
    params = {"per_page": 100}
    top_level_entries = _get_with_pagination(session, url, params)

    # Aggregate all posts by user_id
    # Structure: {user_id: {'user_name': str, 'posts': [entry_dicts]}}
    user_posts: Dict[int, Dict[str, Any]] = {}
    
    def add_entry_to_user(entry: Dict[str, Any]) -> None:
        """Helper to add an entry to the user's post collection"""
        user_id = entry.get("user_id")
        if not user_id:
            return
        
        if user_id not in user_posts:
            user_posts[user_id] = {
                'user_name': entry.get("user_name", f"Student {user_id}"),
                'posts': []
            }
        
        user_posts[user_id]['posts'].append({
            'message': entry.get("message", ""),
            'created_at': entry.get("created_at"),
            'is_reply': entry.get("parent_id") is not None
        })
    
    # Process top-level entries
    for entry in top_level_entries:
        add_entry_to_user(entry)
        
        # Get replies to this entry
        entry_id = entry.get("id")
        if entry_id:
            replies_url = f"{api_base}/courses/{course_id}/discussion_topics/{discussion_id}/entries/{entry_id}/replies"
            try:
                replies = _get_with_pagination(session, replies_url, {})
                for reply in replies:
                    add_entry_to_user(reply)
            except Exception as e:
                logger.warning(f"Could not fetch replies for entry {entry_id}: {e}")

    logger.info(f"Found {len(user_posts)} students with discussion participation")

    students = 0
    files_saved = 0
    student_metadata: Dict[str, Dict[str, Any]] = {}

    # Process each student's aggregated posts
    for user_id, user_data in user_posts.items():
        user_name = user_data['user_name']
        posts = user_data['posts']
        
        if not posts:
            continue
        
        # Try to get more detailed user info from submission data
        sub = submission_data.get(user_id, {})
        user_obj = sub.get("user", {})
        sortable_name = user_obj.get("sortable_name") or user_name
        
        # Build student stem for filename
        stem = f"{_student_stem({'sortable_name': sortable_name, 'name': user_name, 'id': user_id})}_{user_id}"
        
        # Capture metadata
        current_score = sub.get("score") if sub else None
        current_grade = sub.get("grade") if sub else None
        
        # Use the earliest post date as submission date
        submission_date = min((p['created_at'] for p in posts if p.get('created_at')), default=None)
        
        # Capture existing feedback
        current_feedback = ""
        if sub:
            submission_comments = sub.get("submission_comments") or []
            grader_comments = [c for c in submission_comments if c.get("author_id") != user_id]
            if grader_comments:
                latest_comment = grader_comments[-1]
                current_feedback = latest_comment.get("comment") or ""
        
        student_metadata[str(user_id)] = {
            'name': sortable_name,
            'current_score': float(current_score) if current_score is not None else None,
            'current_grade': str(current_grade) if current_grade else None,
            'current_feedback': current_feedback,
            'submission_date': submission_date
        }
        
        # Build HTML content with all posts
        # Separate initial post from replies
        initial_posts = [p for p in posts if not p['is_reply']]
        reply_posts = [p for p in posts if p['is_reply']]
        
        content_parts = []
        
        if initial_posts:
            content_parts.append("<h2>Initial Post</h2>")
            for post in initial_posts:
                content_parts.append(f"<div class='initial-post'>{post['message']}</div>")
        
        if reply_posts:
            content_parts.append("<h2>Comments on Others' Posts</h2>")
            for i, post in enumerate(reply_posts, 1):
                content_parts.append(f"<div class='reply'><strong>Comment {i}:</strong> {post['message']}</div>")
        
        if not content_parts:
            # No content to save
            continue
        
        # Combine all content
        full_content = "\n".join(content_parts)
        
        # Save discussion participation as HTML
        title = f"Discussion: {topic.get('title', 'Entry')}"
        html_text = _wrap_as_html(title, full_content)
        out_path = dest / f"{stem}_discussion.html"
        out_path.write_text(html_text, encoding="utf-8", errors="ignore")
        files_saved += 1
        students += 1

    return students, files_saved, student_metadata