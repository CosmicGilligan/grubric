"""
Dynamic Streamlit UI for rubric-based grading system
Allows entering Canvas assignment IDs per session instead of preconfiguring all assignments
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
import os
import time
import csv
from typing import Any, List, Dict, Tuple, Optional, cast
import logging
from datetime import datetime
import configparser
import requests
from pathlib import Path

# Import components
from canvas_rubric_api import CanvasRubricAPI   # (do not import load_canvas_credentials to avoid name clash)
from course_document_processor import CourseDocumentProcessor
from rubric_assignment_handler import RubricAssignmentHandler
import grade_all
from create_xlsx import create_xlsx
from canvas_submissions import download_submissions_flat

# NEW: provider-agnostic client + wrapper
from client import get_client
from llm_provider import make_llm

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Grade upload helpers (Canvas API) — fixed (no walrus)
# ─────────────────────────────────────────────────────────
import re
from typing import Tuple, Any, Dict, List, Optional

_UID_ANYWHERE = re.compile(r"(\d{4,})")  # grabs a 4+ digit id anywhere in the label
_SCORE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)")

def extract_user_id_from_label(label: str) -> Optional[int]:
    """
    Works for labels like:
     - 'Smith, Jane - 9051950'
     - 'blancoerick_9051950_text.html'
     - 'Doe, John (1234567)'
    Returns int user_id or None.
    """
    if not isinstance(label, str):
        return None
    m = _UID_ANYWHERE.search(label)
    try:
        return int(m.group(1)) if m else None
    except Exception:
        return None

def parse_score_and_comment(feedback: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    Feedback looks like: '8.5/12.0 (C) - Some comment text...'
    Returns (score, max_points, comment_text). If no score found, score=None.
    """
    if not isinstance(feedback, str):
        return None, None, ""
    score: Optional[float] = None
    max_pts: Optional[float] = None

    m = _SCORE_RE.match(feedback)  # ← fixed (no walrus)
    if m:
        try:
            score = float(m.group(1))
            max_pts = float(m.group(2))
        except (ValueError, TypeError):
            score = None
            max_pts = None

    # comment = text after the first ' - ' if present, else whole feedback
    dash_idx = feedback.find(" - ")
    comment = feedback[dash_idx + 3:] if dash_idx != -1 else feedback
    return score, max_pts, comment.strip()

def upload_grade_and_comment(
    api_base: str,
    token: str,
    course_id: int,
    assignment_id: int,
    user_id: int,
    score: Optional[float],
    comment: str
) -> Dict[str, Any]:
    """
    PUT /courses/:course_id/assignments/:assignment_id/submissions/:user_id
      - submission[posted_grade]: numeric points or letter (we send points)
      - comment[text_comment]: feedback
    """
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    data: Dict[str, Any] = {}
    if score is not None:
        data["submission[posted_grade]"] = str(score)
    if comment:
        data["comment[text_comment]"] = comment[:10000]
    r = requests.put(url, headers=headers, data=data)
    r.raise_for_status()
    return r.json()

def upload_all_grades(
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
        try:
            label = row[0] if len(row) > 0 else ""
            feedback = row[2] if len(row) > 2 else ""
            user_id = extract_user_id_from_label(label)
            if user_id is None:
                failures.append((label, "No Canvas user_id found in label"))
                continue
            score, _, comment = parse_score_and_comment(feedback)
            upload_grade_and_comment(api_base, token, int(course_id), int(assignment_id), user_id, score, comment)
            successes += 1
        except Exception as e:
            failures.append((row[0] if row else "<unknown>", str(e)))
    return successes, len(failures), failures


# ──────────────────────────────────────────────────────────────────────────────
# Credentials (reads ~/canvas-secrets.key, normalizes API base once)
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_api_url(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/api/v1"):
        url = url[:-len("/api/v1")]
    return url + "/api/v1"

def load_canvas_credentials_local() -> Tuple[str, str]:
    key_path = Path.home() / "canvas-secrets.key"
    lines = [ln.strip() for ln in key_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("canvas-secrets.key must have URL on line 1 and token on line 2")
    api_base = _normalize_api_url(lines[0])
    token = lines[1]
    return api_base, token

# initialize once (stash in session_state)
if "canvas_url" not in st.session_state or "canvas_token" not in st.session_state:
    try:
        st.session_state.canvas_url, st.session_state.canvas_token = load_canvas_credentials_local()
    except Exception as e:
        st.error(str(e))
        st.stop()
canvas_url = st.session_state.canvas_url       # already normalized to .../api/v1
token = st.session_state.canvas_token

# ──────────────────────────────────────────────────────────────────────────────
# Helpers for rubric fetching and state gating
# ──────────────────────────────────────────────────────────────────────────────
from typing import List as _List, Dict as _Dict  # avoid confusion in hints below

def fetch_assignment_rubric(api_base: str, token: str, course_id: int, assignment_id: int) -> Optional[_List[_Dict[str, Any]]]:
    """Return the Canvas rubric as a list of criterion dicts, or None if none is attached."""
    url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}"
    headers = {"Authorization": f"Bearer {token}"}
    params = [("include[]", "rubric")]
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    data: Dict[str, Any] = r.json()
    rubric = data.get("rubric")
    if isinstance(rubric, list):
        return rubric
    return None

def _init_state():
    st.session_state.setdefault("assignment_handler", None)
    st.session_state.setdefault("rubric", None)                     # Optional[List[Dict[str, Any]]]
    st.session_state.setdefault("rubric_loaded_for", None)          # (course_id, assignment_id)
    st.session_state.setdefault("rubric_total_points", 0.0)

    st.session_state.setdefault("entryList", [])
    st.session_state.setdefault("submissions_ready_for", None)      # (course_id, assignment_id)

    st.session_state.setdefault("ready_to_grade", False)
    st.session_state.setdefault("stop_processing", False)

def _recompute_ready_flag():
    st.session_state.ready_to_grade = (
        st.session_state.rubric is not None
        and st.session_state.submissions_ready_for is not None
        and st.session_state.rubric_loaded_for == st.session_state.submissions_ready_for
        and bool(st.session_state.entryList)
    )

# ──────────────────────────────────────────────────────────────────────────────
# Page configuration
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Dynamic Rubric Grading",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)
_init_state()

# ──────────────────────────────────────────────────────────────────────────────
# Provider & Model selection (NEW)
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_llm_cached(provider: str):
    """Create and cache a provider-agnostic LLM wrapper for the selected provider."""
    raw = get_client(provider)  # reads ~/anthropic.key, ~/openai.key, ~/genai.key
    return make_llm(provider, raw)

st.markdown("## Dynamic Rubric-Based Grading System")
st.markdown("*Enter Canvas assignment ID to automatically load rubric and grade submissions*")

with st.sidebar:
    st.header("Model Settings")
    provider = st.selectbox("AI Provider", ["anthropic", "openai", "google"], index=0)
    model_choices = {
        "anthropic": {
            "Claude 3.5 Sonnet (rec)": "claude-3-5-sonnet-20241022",
            "Claude 3 Opus": "claude-3-opus-20240229",
            "Claude 3 Haiku": "claude-3-haiku-20240307",
        },
        # in rubric_grade_ui.py sidebar model list
        "openai": {
            "GPT-4o (latest)": "chatgpt-4o-latest",
            "GPT-4o mini": "gpt-4o-mini",
            "GPT-4 Turbo": "gpt-4-turbo",
        },
        "google": {
            "Gemini 1.5 Pro": "gemini-1.5-pro",
            "Gemini 1.5 Flash": "gemini-1.5-flash",
        },
    }
    model_name = st.selectbox("Model", list(model_choices[provider].keys()))
    selected_model = model_choices[provider][model_name]

llm = get_llm_cached(provider)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration management
# ──────────────────────────────────────────────────────────────────────────────
class DynamicRubricConfig:
    def __init__(self, config_file="config_rubric.ini"):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
        else:
            st.error(f"Configuration file not found: {self.config_file}")
    
    def get_courses(self) -> List[Tuple[str, str, str]]:
        """Get courses as (display_name, course_id, documents_path)"""
        courses = []
        if 'COURSES' in self.config:
            for key, value in self.config['COURSES'].items():
                parts = [part.strip() for part in value.split(',')]
                if len(parts) == 3:
                    display_name, course_id, documents_path = parts
                    courses.append((display_name, course_id, documents_path))
        return courses
    
    def get_setting(self, setting_name: str, default: str = "") -> str:
        if 'SETTINGS' in self.config and setting_name in self.config['SETTINGS']:
            return self.config['SETTINGS'][setting_name]
        return default
    
    def get_path(self, path_name: str, default: str = "") -> str:
        if 'PATHS' in self.config and path_name in self.config['PATHS']:
            return self.config['PATHS'][path_name]
        return default
    
    def save_assignment_to_history(self, assignment_name: str, assignment_id: str, course_id: str):
        """Save assignment to history for quick access"""
        if 'ASSIGNMENT_HISTORY' not in self.config:
            self.config.add_section('ASSIGNMENT_HISTORY')
        
        value = f"{assignment_id}, {course_id}, {datetime.now().strftime('%Y-%m-%d')}"
        self.config['ASSIGNMENT_HISTORY'][assignment_name.replace(' ', '_').lower()] = value
        
        try:
            with open(self.config_file, 'w') as f:
                self.config.write(f)
        except Exception as e:
            logger.error(f"Error saving assignment history: {e}")
    
    def get_assignment_history(self) -> List[Tuple[str, str, str]]:
        """Get recent assignments as (name, assignment_id, course_id)"""
        history = []
        if 'ASSIGNMENT_HISTORY' in self.config:
            for key, value in self.config['ASSIGNMENT_HISTORY'].items():
                parts = [part.strip() for part in value.split(',')]
                if len(parts) >= 2:
                    assignment_id, course_id = parts[0], parts[1]
                    name = key.replace('_', ' ').title()
                    history.append((name, assignment_id, course_id))
        return history

# Initialize configuration
config = DynamicRubricConfig()

# ──────────────────────────────────────────────────────────────────────────────
# Initialize Canvas API client
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_canvas_api():
    try:
        api_url, api_token = load_canvas_credentials_local()
        return CanvasRubricAPI(api_url, api_token)
    except Exception as e:
        st.error(f"Failed to initialize Canvas API: {e}")
        st.stop()

canvas_api = get_canvas_api()

# ──────────────────────────────────────────────────────────────────────────────
# Grading runner
# ──────────────────────────────────────────────────────────────────────────────
def run_grading_process(handler, entryList, model, total_points):
    """Perform the grading process"""
    progress_bar = st.progress(0)
    status_text = st.empty()
    current_student = st.empty()
    stop_container = st.empty()

    with stop_container.container():
        if st.button("Stop Processing", key="stop_grading"):
            st.session_state.stop_processing = True
            st.warning("Stop requested...")

    try:
        submissions = [(entry[0], entry[1]) for entry in entryList]
        results = []
        total = len(submissions)

        for i, (student_name, submission_text) in enumerate(submissions):
            if st.session_state.get('stop_processing', False):
                status_text.text("Processing stopped by user")
                break

            progress = i / total if total else 1.0
            progress_bar.progress(progress)
            status_text.text(f"Grading {student_name} ({i+1}/{total})")
            current_student.info(f"Currently grading: **{student_name}**")

            result = handler.grade_submission(submission_text, student_name, model)
            results.append(result)

            logger.info(f"Graded {student_name}: {result['score']}/{result['max_score']})")

        current_student.empty()
        stop_container.empty()
        progress_bar.progress(1.0)
        status_text.text("Grading completed!")

        for i, result in enumerate(results):
            if i < len(entryList):
                # Feedback already formatted by handler, use as-is
                entryList[i][2] = result['feedback']

        submissions_dir = config.get_path('submissions_directory', './submissions/')
        os.makedirs(submissions_dir, exist_ok=True)
        filename = os.path.join(submissions_dir, 'completions.csv')

        with open(filename, 'w+', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(entryList)

        create_xlsx(filename)

        if results:
            avg_score = sum(r['score'] for r in results) / len(results)
            total_points = results[0]['max_score'] if results else 100
            
            # Calculate score ranges instead of letter grades
            score_ranges = {
                'A (90%+)': sum(1 for r in results if r['score'] >= r['max_score'] * 0.9),
                'B (80-89%)': sum(1 for r in results if r['max_score'] * 0.8 <= r['score'] < r['max_score'] * 0.9),
                'C (70-79%)': sum(1 for r in results if r['max_score'] * 0.7 <= r['score'] < r['max_score'] * 0.8),
                'D (60-69%)': sum(1 for r in results if r['max_score'] * 0.6 <= r['score'] < r['max_score'] * 0.7),
                'F (<60%)': sum(1 for r in results if r['score'] < r['max_score'] * 0.6)
            }

            st.success(f"Completed! Average score: {avg_score:.1f}/{total_points}")
            col_a, col_b, col_c, col_d, col_f = st.columns(5)
            with col_a: st.metric("A's", score_ranges['A (90%+)'])
            with col_b: st.metric("B's", score_ranges['B (80-89%)'])
            with col_c: st.metric("C's", score_ranges['C (70-79%)'])
            with col_d: st.metric("D's", score_ranges['D (60-69%)'])
            with col_f: st.metric("F's", score_ranges['F (<60%)'])

        st.balloons()
    except Exception as e:
        st.error(f"Error during grading: {e}")
        logger.error(f"Grading error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("Step 1: Select Course")
courses = config.get_courses()
if not courses:
    st.error("No courses configured. Please check your config file.")
    st.stop()

course_options = {f"{name} ({course_id})": (name, course_id, docs_path) 
                 for name, course_id, docs_path in courses}

selected_course_display = st.selectbox(
    "Choose your course section:",
    options=list(course_options.keys()),
    help="Select the course section you want to grade"
)
selected_course_name, selected_course_id, documents_path = course_options[selected_course_display]
st.session_state.selected_course = (selected_course_name, selected_course_id, documents_path)

# Step 2: Assignment ID Entry
st.subheader("Step 2: Enter Assignment Details")
col1, col2 = st.columns([2, 1])
with col1:
    assignment_id = st.text_input(
        "Canvas Assignment ID:",
        help="Find this in your Canvas assignment URL: .../assignments/[ID]",
        placeholder="e.g., 123456"
    )
    assignment_name = st.text_input(
        "Assignment Name (for your reference):",
        placeholder="e.g., Module 3 Essay",
        help="Optional: This will be saved for quick access later"
    )
with col2:
    st.markdown("**Recent Assignments:**")
    history = config.get_assignment_history()
    if history:
        for name, hist_id, hist_course in history[-5:]:
            if st.button(f"{name} ({hist_id})", key=f"history_{hist_id}"):
                st.session_state.assignment_id_input = hist_id
                st.session_state.assignment_name_input = name
                st.rerun()
    else:
        st.caption("No recent assignments")

# Step 3: Load Rubric
st.subheader("Step 3: Load Rubric")
if assignment_id and st.button("Load Assignment Rubric", type="primary"):
    with st.spinner("Loading rubric and course documents..."):
        try:
            handler = RubricAssignmentHandler(
                assignment_key=f"dynamic_{assignment_id}",
                display_name=assignment_name or f"Assignment {assignment_id}",
                canvas_assignment_id=assignment_id,
                course_id=selected_course_id,
                course_documents_path=documents_path,
                # CHANGED: pass unified LLM instead of Anthropic-specific client
                llm=llm
            )
            st.session_state.assignment_handler = handler

            # Fetch rubric JSON from Canvas so we know total points and criteria
            rubric_items = fetch_assignment_rubric(canvas_url, token, int(selected_course_id), int(assignment_id))
            if rubric_items is not None:
                st.session_state.rubric = rubric_items  # List[Dict[str, Any]]
                st.session_state.rubric_total_points = float(sum((c.get("points") or 0) for c in rubric_items))
                st.session_state.rubric_loaded_for = (int(selected_course_id), int(assignment_id))
                st.success("Rubric loaded.")
            else:
                st.warning("No rubric attached to this assignment in Canvas.")
                st.session_state.rubric = None
                st.session_state.rubric_total_points = 0.0
                st.session_state.rubric_loaded_for = None

            if assignment_name:
                config.save_assignment_to_history(assignment_name, assignment_id, selected_course_id)

            _recompute_ready_flag()
        except Exception as e:
            st.error(f"Error loading assignment: {e}")
            logger.error(f"Assignment loading error: {e}")

# Step 3.5: Assignment Overview (only if rubric is loaded)
if st.session_state.rubric is not None and st.session_state.assignment_handler:
    handler = st.session_state.assignment_handler
    rubric_items = cast(List[Dict[str, Any]], st.session_state.rubric)
    total_points = st.session_state.rubric_total_points or handler.total_points
    criterion_count = len(rubric_items)
    document_chunks = 0
    docproc = getattr(handler, "document_processor", None)
    df_obj = getattr(docproc, "df", None) if docproc is not None else None

    if isinstance(df_obj, pd.DataFrame):
        document_chunks = len(df_obj)

    st.subheader("Assignment Overview")
    col1, col2, col3 = st.columns(3)
    with col1: st.metric("Total Points", total_points)
    with col2: st.metric("Rubric Criteria", criterion_count)
    with col3: st.metric("Course Documents", document_chunks)

    with st.expander("View Rubric Criteria", expanded=False):
        for i, c in enumerate(rubric_items, 1):
            desc = c.get("description") or "(no description)"
            pts = c.get("points") or 0
            st.write(f"**{i}. {desc}** ({pts} pts)")
            ld = c.get("long_description")
            if isinstance(ld, str) and ld.strip():
                st.caption(ld)

# Step 4: Load Submissions  (single block)
st.subheader("Step 4: Load Submissions")
if assignment_id and st.button("Download submissions from Canvas"):
    with st.spinner("Downloading submissions..."):
        course_id = int(selected_course_id)
        asg_id = int(assignment_id)
        students, files = download_submissions_flat(
            canvas_base_url=canvas_url,     # normalized base
            token=token,
            course_id=course_id,
            assignment_id=asg_id,
            dest_dir="./submissions",
            clean_dest=True
        )
        # Build the entry list immediately so the grader can open
        st.session_state.entryList = grade_all.makeEntryList(selected_course_id)
        st.session_state.submissions_ready_for = (course_id, asg_id)
        st.success(f"Pulled {files} files for {students} students into ./submissions")
        _recompute_ready_flag()
        st.rerun()

# Optional manual refresh (does not re-download, just rescans the folder)
if st.button("Refresh Submissions"):
    st.session_state.entryList = grade_all.makeEntryList(selected_course_id)
    if assignment_id:
        st.session_state.submissions_ready_for = (int(selected_course_id), int(assignment_id))
    _recompute_ready_flag()
    st.success(f"Refreshed: {len(st.session_state.entryList)} submissions loaded")

# Step 5: Grade Submissions (only when both rubric & submissions match the same assignment)
if st.session_state.ready_to_grade:
    st.subheader("Step 5: Grade Submissions")
    entryList = st.session_state.entryList
    handler = st.session_state.assignment_handler
    total_points = st.session_state.rubric_total_points or handler.total_points

    st.info(f"Ready to grade {len(entryList)} submissions using **{provider} → {selected_model}**")

    if st.button("Start Grading", type="primary"):
        run_grading_process(handler, entryList, selected_model, total_points)

# ─────────────────────────────────────────────────────────
# Step 6: Upload Grades & Comments to Canvas
# ─────────────────────────────────────────────────────────
from grade_uploader import upload_all_from_entrylist, upload_all_from_xlsx

st.subheader("Step 6: Upload Grades to Canvas")

source = st.radio(
    "Upload source",
    ["Current session (graded list)", "Saved XLSX file"],
    index=0,
    horizontal=True
)

if source == "Current session (graded list)":
    if st.button("Upload current session grades", type="primary"):
        with st.spinner("Uploading grades..."):
            ok, fail, failures = upload_all_from_entrylist(
                api_base=canvas_url,
                token=token,
                course_id=int(selected_course_id),
                assignment_id=int(assignment_id),
                entry_list=st.session_state.entryList,
            )
        st.success(f"Uploaded {ok} grades")
        if fail:
            st.warning(f"{fail} failed")
            with st.expander("View failures"):
                for who, why in failures:
                    st.write(f"• {who} — {why}")
else:
    up = st.file_uploader("Choose a saved XLSX (from create_xlsx)", type=["xlsx"])
    respect_flag = st.checkbox("Respect 'Upload?' column", value=True)
    if up and st.button("Upload grades from XLSX", type="primary"):
        import tempfile, shutil, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(up.read())
            tmp_path = tmp.name
        with st.spinner("Uploading from XLSX..."):
            ok, fail, failures = upload_all_from_xlsx(
                api_base=canvas_url,
                token=token,
                course_id=int(selected_course_id),
                assignment_id=int(assignment_id),
                xlsx_path=tmp_path,
                respect_upload_flag=respect_flag
            )
        st.success(f"Uploaded {ok} grades from XLSX")
        if fail:
            st.warning(f"{fail} failed")
            with st.expander("View failures"):
                for who, why in failures:
                    st.write(f"• {who} — {why}")
        os.unlink(tmp_path)

# Sidebar
with st.sidebar:
    st.header("Current Session")
    if "selected_course" in st.session_state and st.session_state.selected_course:
        course_name, course_id, _ = st.session_state.selected_course
        st.write(f"**Course:** {course_name}")
        st.write(f"**Course ID:** {course_id}")

    if st.session_state.assignment_handler:
        handler = st.session_state.assignment_handler
        st.write(f"**Assignment:** {handler.display_name}")
        st.write(f"**Canvas ID:** {handler.canvas_assignment_id}")
        st.write(f"**Total Points:** {st.session_state.rubric_total_points or handler.total_points}")

    if st.session_state.entryList:
        st.write(f"**Submissions:** {len(st.session_state.entryList)} loaded")

    st.markdown("---")
    st.header("How to Find Canvas Assignment ID")
    st.markdown("""
    1. Go to your Canvas course  
    2. Click on "Assignments"  
    3. Click on the specific assignment  
    4. Look at the URL in your browser  
    5. The assignment ID is the number at the end  

    Example URL: `https://school.instructure.com/courses/12345/assignments/67890`  
    Assignment ID = **67890**
    """)

    st.markdown("---")
    st.header("System Status")
    st.success("Canvas API: Connected")
    st.success(f"LLM: {provider.title()} ready")
