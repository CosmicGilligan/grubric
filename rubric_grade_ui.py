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
from canvas_rubric_api import CanvasRubricAPI
from course_document_processor import CourseDocumentProcessor
from rubric_assignment_handler import RubricAssignmentHandler
from create_xlsx import create_xlsx
from canvas_submissions import download_submissions_flat

# NEW: provider-agnostic client + wrapper
from client import get_client
from llm_provider import make_llm

# NEW: live model catalog fetcher (replaces stale config-file model lists)
from model_catalog import fetch_anthropic_models, fetch_openai_models, fetch_google_models

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Grade upload helpers (Canvas API)
# ═══════════════════════════════════════════════════════════════════════════
import re
from typing import Tuple, Any, Dict, List, Optional

_UID_ANYWHERE = re.compile(r"(\d{4,})")
_SCORE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)")

def extract_user_id_from_label(label: str) -> Optional[int]:
    """Extract Canvas user_id from label"""
    if not isinstance(label, str):
        return None
    m = _UID_ANYWHERE.search(label)
    try:
        return int(m.group(1)) if m else None
    except Exception:
        return None

def parse_score_and_comment(feedback: str) -> Tuple[Optional[float], Optional[float], str]:
    """Parse feedback string into score, max_points, and comment"""
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
    """Upload a single grade and comment to Canvas"""
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
    """Upload all grades from entry_list"""
    successes = 0
    failures: List[Tuple[str, str]] = []
    for row in entry_list:
        try:
            label = row[0] if len(row) > 0 else ""
            score_str = row[2] if len(row) > 2 else ""
            feedback = row[3] if len(row) > 3 else ""
            user_id = extract_user_id_from_label(label)
            if user_id is None:
                failures.append((label, "No Canvas user_id found in label"))
                continue
            
            # Convert score to float, default to None if empty/invalid
            try:
                score = float(score_str) if score_str and score_str.strip() else None
            except (ValueError, TypeError):
                score = None
                
            # Use feedback directly as comment (no need to parse score from it)
            comment = feedback
            upload_grade_and_comment(api_base, token, int(course_id), int(assignment_id), user_id, score, comment)
            successes += 1
        except Exception as e:
            failures.append((row[0] if row else "<unknown>", str(e)))
    return successes, len(failures), failures

# ═══════════════════════════════════════════════════════════════════════════
# Credentials
# ═══════════════════════════════════════════════════════════════════════════
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

# Initialize credentials once
if "canvas_url" not in st.session_state or "canvas_token" not in st.session_state:
    try:
        st.session_state.canvas_url, st.session_state.canvas_token = load_canvas_credentials_local()
    except Exception as e:
        st.error(str(e))
        st.stop()
canvas_url = st.session_state.canvas_url
token = st.session_state.canvas_token

# Add this function to handle fetching rubrics for discussions
# This should go in the same file where you fetch assignment rubrics

def fetch_discussion_rubric(
    api_base: str,
    token: str,
    course_id: int,
    discussion_id: int
) -> List[Dict[str, Any]]:
    """
    Fetch rubric for a Canvas discussion topic.
    Discussions use the discussion_topics endpoint but may have an associated assignment.
    """
    import requests
    
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    
    # First, get the discussion topic to find the assignment ID
    topic_url = f"{api_base}/courses/{course_id}/discussion_topics/{discussion_id}"
    topic_response = session.get(topic_url, params={"include[]": "assignment"})
    topic_response.raise_for_status()
    topic = topic_response.json()
    
    # Get the assignment object from the discussion
    assignment = topic.get("assignment")
    if not assignment:
        raise ValueError(f"Discussion {discussion_id} is not graded (no associated assignment)")
    
    # Get the assignment ID
    assignment_id = assignment.get("id")
    if not assignment_id:
        raise ValueError(f"Could not find assignment ID for discussion {discussion_id}")
    
    # Now fetch the rubric from the assignment
    rubric_url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}"
    rubric_response = session.get(rubric_url, params={"include[]": "rubric"})
    rubric_response.raise_for_status()
    assignment_data = rubric_response.json()
    
    rubric = assignment_data.get("rubric") or []
    if not rubric:
        raise ValueError(f"No rubric found for discussion {discussion_id}")
    
    return rubric




# ═══════════════════════════════════════════════════════════════════════════
# Helpers for rubric fetching (UPDATED TO HANDLE DISCUSSIONS)
# ═══════════════════════════════════════════════════════════════════════════
from typing import List as _List, Dict as _Dict, Tuple as _Tuple

def fetch_assignment_or_discussion_rubric(
    api_base: str, 
    token: str, 
    course_id: int, 
    item_id: int,
    is_discussion: bool = False
) -> Tuple[Optional[_List[_Dict[str, Any]]], Optional[float]]:
    """
    Return the Canvas rubric as a list of criterion dicts and the points_possible from the assignment.
    Handles both assignments and discussions by using the correct endpoint.
    """
    headers = {"Authorization": f"Bearer {token}"}
    
    # For discussions, we need to first find the associated assignment ID
    if is_discussion:
        logger.info(f"Fetching discussion topic {item_id} to find associated assignment")
        discussion_url = f"{api_base}/courses/{course_id}/discussion_topics/{item_id}"
        
        r = requests.get(discussion_url, headers=headers)
        r.raise_for_status()
        discussion_data = r.json()
        
        # Get the assignment ID from the discussion
        assignment_id = discussion_data.get('assignment_id')
        
        if not assignment_id:
            logger.error(f"Discussion topic {item_id} has no associated assignment (not graded)")
            return None, None
        
        logger.info(f"Found assignment ID {assignment_id} for discussion topic {item_id}")
        
        # Now fetch the assignment with its rubric
        url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}"
    else:
        # Regular assignment
        url = f"{api_base}/courses/{course_id}/assignments/{item_id}"
    
    # Fetch the assignment with rubric
    params = [("include[]", "rubric")]
    
    logger.info(f"Fetching rubric from: {url}")
    
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    data: Dict[str, Any] = r.json()
    
    rubric = data.get("rubric")
    points_possible = data.get('points_possible')
    
    if isinstance(rubric, list) and len(rubric) > 0:
        # If points_possible is 0 or None, calculate from rubric criteria
        if not points_possible or points_possible == 0:
            calculated_points = sum(float(criterion.get('points', 0)) for criterion in rubric)
            logger.info(f"Assignment points_possible is {points_possible}, calculating from rubric: {calculated_points}")
            points_possible = calculated_points
        
        logger.info(f"Successfully loaded rubric with {len(rubric)} criteria, total points: {points_possible}")
        return rubric, float(points_possible) if points_possible is not None else None
    
    logger.warning("No rubric found")
    return None, None

def _init_state():
    st.session_state.setdefault("assignment_handler", None)
    st.session_state.setdefault("rubric", None)
    st.session_state.setdefault("rubric_loaded_for", None)
    st.session_state.setdefault("rubric_total_points", 0.0)
    st.session_state.setdefault("is_discussion", False)  # NEW: Track if it's a discussion
    
    # Initialize scoring defaults (12 points for assignments, will be overridden by rubric)
    st.session_state.setdefault("threshold_score", 8.0)
    st.session_state.setdefault("total_possible_points", 12.0)

    st.session_state.setdefault("entryList", [])
    st.session_state.setdefault("submissions_ready_for", None)
    st.session_state.setdefault("student_metadata", {})
    st.session_state.setdefault("original_entries", {})

    st.session_state.setdefault("ready_to_grade", False)
    st.session_state.setdefault("stop_processing", False)
    st.session_state.setdefault("grading_mode", "all")
    st.session_state.setdefault("selected_students", set())
    st.session_state.setdefault("session_graded_ids", set())  # tracks user IDs graded THIS session
    st.session_state.setdefault("keep_highest_score", False)

def _recompute_ready_flag():
    st.session_state.ready_to_grade = (
        st.session_state.rubric is not None
        and st.session_state.submissions_ready_for is not None
        and st.session_state.rubric_loaded_for == st.session_state.submissions_ready_for
        and bool(st.session_state.entryList)
    )

# ═══════════════════════════════════════════════════════════════════════════
# Page configuration
# ═══════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Dynamic Rubric Grading",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)
_init_state()

# ═══════════════════════════════════════════════════════════════════════════
# Configuration management - Initialize before sidebar
# ═══════════════════════════════════════════════════════════════════════════
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
    
    def get_key_file(self, provider: str) -> str:
        """Get the API key file path for a provider from [API_SETTINGS].
        Looks for '<provider>_key_file' (e.g. anthropic_key_file)."""
        if 'API_SETTINGS' in self.config:
            return self.config['API_SETTINGS'].get(f"{provider}_key_file", "")
        return ""
    
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
    
    def get_models_for_provider(self, provider: str) -> Dict[str, str]:
        """
        Get models for a specific provider from config (OFFLINE FALLBACK ONLY —
        the sidebar prefers live results from the provider's API; this is only
        used if that fetch fails or no key file is configured).
        Returns dict of {display_name: model_id}
        
        Args:
            provider: 'anthropic', 'openai', or 'google'
        
        Returns:
            Dictionary mapping display names to model IDs
        """
        section_name = f"{provider.upper()}_MODELS"
        models = {}
        
        if section_name in self.config:
            for key, value in self.config[section_name].items():
                parts = [part.strip() for part in value.split(',')]
                if len(parts) == 2:
                    display_name, model_id = parts
                    models[display_name] = model_id
                else:
                    logger.warning(f"Invalid model config for {provider}.{key}: {value}")
        
        return models
    
    def get_leniency_multiplier(self) -> float:
        """
        Get grading leniency multiplier from config.
        Returns float between 0.5 and 2.0 (default 1.0 = no adjustment)
        
        Examples:
            1.0 = no adjustment
            1.2 = 20% boost (more lenient)
            0.9 = 10% penalty (stricter)
        """
        try:
            multiplier_str = self.get_setting('leniency_multiplier', '1.0')
            multiplier = float(multiplier_str)
            # Clamp between 0.5 and 2.0 for safety
            if multiplier < 0.5 or multiplier > 2.0:
                logger.warning(f"Leniency multiplier {multiplier} out of range [0.5, 2.0], using 1.0")
                return 1.0
            return multiplier
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid leniency_multiplier in config: {e}, using 1.0")
            return 1.0

config = DynamicRubricConfig()

# ═══════════════════════════════════════════════════════════════════════════
# Live model catalog (replaces the old hardcoded validate_models_at_startup)
# ═══════════════════════════════════════════════════════════════════════════
_PROVIDER_FETCHERS = {
    "anthropic": fetch_anthropic_models,
    "openai": fetch_openai_models,
    "google": fetch_google_models,
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_models_cached(provider: str, key_file: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """
    Live-fetches models for `provider` and returns
    ([(display_name, model_id), ...], error_message_or_None).
    Cached for an hour per (provider, key_file) so a Streamlit rerun doesn't
    hit the provider's API every time a widget changes.
    """
    try:
        models = _PROVIDER_FETCHERS[provider](key_file)
        return [(m.display_name, m.id) for m in models], None
    except Exception as e:
        return [], str(e)

def get_model_choices(provider: str) -> Dict[str, str]:
    """
    Live model list for `provider`, falling back to the static
    [*_MODELS] section in config_rubric.ini if the key file is missing
    or the API call fails (e.g. no network).
    """
    key_file = config.get_key_file(provider)
    if key_file and os.path.exists(key_file):
        pairs, error = fetch_models_cached(provider, key_file)
        if pairs:
            return dict(pairs)
        if error:
            st.sidebar.warning(f"Couldn't fetch live {provider} models ({error}) — using config file list.")
    else:
        st.sidebar.info(f"No {provider}_key_file configured — using config file list for {provider}.")
    return config.get_models_for_provider(provider)

# ═══════════════════════════════════════════════════════════════════════════
# Provider & Model selection
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_resource
def get_llm_cached(provider: str):
    """Create and cache a provider-agnostic LLM wrapper for the selected provider."""
    raw = get_client(provider)
    return make_llm(provider, raw)

st.markdown("## Dynamic Rubric-Based Grading System")
st.markdown("*Enter Canvas assignment ID to automatically load rubric and grade submissions*")

with st.sidebar:
    st.header("Model Settings")
    provider = st.selectbox("AI Provider", ["anthropic", "openai", "google"], index=0)

    col_refresh, _ = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 Refresh models"):
            fetch_models_cached.clear()
            st.rerun()

    model_choices = get_model_choices(provider)
    if not model_choices:
        st.error(f"No {provider} models available (live fetch failed and no fallback configured).")
        st.stop()

    model_name = st.selectbox("Model", list(model_choices.keys()))
    selected_model = model_choices[model_name]
    
    # Display model info
    st.caption(f"Model ID: `{selected_model}`")

llm = get_llm_cached(provider)

# ═══════════════════════════════════════════════════════════════════════════
# Initialize Canvas API client
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_resource
def get_canvas_api():
    try:
        api_url, api_token = load_canvas_credentials_local()
        return CanvasRubricAPI(api_url, api_token)
    except Exception as e:
        st.error(f"Failed to initialize Canvas API: {e}")
        st.stop()

canvas_api = get_canvas_api()

# ═══════════════════════════════════════════════════════════════════════════
# Grading runner
# ═══════════════════════════════════════════════════════════════════════════
def run_grading_process(handler, entryList, model, total_points, mode="all"):
    """Perform the grading process
    
    Args:
        handler: RubricAssignmentHandler instance
        entryList: List of entries to grade (already filtered if mode="selective")
        model: Model name to use
        total_points: Total points for the assignment
        mode: "all" or "selective"
    """
    # Pick up the latest required-elements text, so edits made after loading the rubric still apply
    handler.required_elements = (
        st.session_state.get(f"required_elements_{handler.canvas_assignment_id}") or ""
    ).strip()

    # Same for leniency: use whatever is in the box now, even if it changed after loading
    _len_val = st.session_state.get(f"leniency_{handler.canvas_assignment_id}")
    if _len_val is not None:
        handler.leniency_multiplier = max(0.5, min(2.0, float(_len_val)))

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

        # Update entryList with new scores and feedback
        for i, result in enumerate(results):
            if i < len(entryList):
                entryList[i][2] = str(result['score'])  # Score column
                entryList[i][3] = result['feedback']    # Feedback column

        # Keep highest score if toggle is on (selective mode only)
        if mode == "selective" and st.session_state.get("keep_highest_score", False):
            metadata = st.session_state.get("student_metadata", {})
            retained_count = 0
            for entry in entryList:
                user_id = str(extract_user_id_from_label(entry[0]))
                old_score = (metadata.get(user_id) or {}).get("current_score")
                try:
                    new_score = float(entry[2]) if entry[2] else None
                except (ValueError, TypeError):
                    new_score = None
                if old_score is not None and new_score is not None and new_score < old_score:
                    entry[2] = str(old_score)
                    entry[3] = f"Highest score retained. {entry[3]}"
                    retained_count += 1
            if retained_count:
                st.info(f"↑ Highest score retained for {retained_count} student(s) whose resubmission scored lower")

        # Merge with original entries if selective grading
        final_entry_list = []
        if mode == "selective":
            # Create a map of graded entries
            graded_map = {}
            for entry in entryList:
                user_id = extract_user_id_from_label(entry[0])
                if user_id:
                    graded_map[str(user_id)] = entry
            
            # Track which IDs were graded this session (used to restrict upload)
            st.session_state.session_graded_ids = set(graded_map.keys())

            # Build final list: use graded version if available, otherwise original
            for user_id, original_entry in st.session_state.original_entries.items():
                if user_id in graded_map:
                    final_entry_list.append(graded_map[user_id])
                else:
                    final_entry_list.append(original_entry)
            
            preserved_count = len(final_entry_list) - len(graded_map)
            st.info(f"✓ Merged results: {len(graded_map)} newly graded + {preserved_count} preserved with original grades/feedback")
        else:
            # All-students mode: every entry is fresh, upload all
            st.session_state.session_graded_ids = {
                str(extract_user_id_from_label(e[0])) for e in entryList
                if extract_user_id_from_label(e[0])
            }
            final_entry_list = entryList

        # Write to CSV
        submissions_dir = config.get_path('submissions_directory', './submissions/')
        os.makedirs(submissions_dir, exist_ok=True)
        filename = os.path.join(submissions_dir, 'completions.csv')

        with open(filename, 'w+', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Write header row
            writer.writerow(['Label', 'Submission Text', 'Score', 'Feedback'])
            writer.writerows(final_entry_list)

        # Get course_id and assignment_id from session state
        if st.session_state.submissions_ready_for:
            course_id, assignment_id = st.session_state.submissions_ready_for
            create_xlsx(filename, course_id=course_id, assignment_id=assignment_id)
        else:
            create_xlsx(filename)  # Fallback to auto-detection

        if results:
            avg_score = sum(r['score'] for r in results) / len(results)
            total_points = results[0]['max_score'] if results else 100
            
            # Calculate score ranges
            score_ranges = {
                'A (90%+)': sum(1 for r in results if r['score'] >= r['max_score'] * 0.9),
                'B (80-89%)': sum(1 for r in results if r['max_score'] * 0.8 <= r['score'] < r['max_score'] * 0.9),
                'C (70-79%)': sum(1 for r in results if r['max_score'] * 0.7 <= r['score'] < r['max_score'] * 0.8),
                'D (60-69%)': sum(1 for r in results if r['max_score'] * 0.6 <= r['score'] < r['max_score'] * 0.7),
                'F (<60%)': sum(1 for r in results if r['score'] < r['max_score'] * 0.6)
            }

            if mode == "selective":
                st.success(f"Completed! Graded {len(results)} selected submissions. Average score: {avg_score:.1f}/{total_points}")
            else:
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

# ═══════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════════════
# Step 2: Assignment ID Entry (WITH DISCUSSION CHECKBOX)
# ═══════════════════════════════════════════════════════════════════════════
# Add this to your rubric_grade_ui.py file

# ==== STEP 2: Assignment/Discussion ID Entry ====
st.subheader("Step 2: Enter Assignment or Discussion Details")

col1, col2 = st.columns([3, 1])

with col1:
    assignment_id = st.text_input(
        "Canvas ID:",
        help="Assignment ID from .../assignments/[ID] or Discussion ID from .../discussion_topics/[ID]",
        placeholder="e.g., 123456"
    )

with col2:
    is_discussion = st.checkbox(
        "Discussion?", 
        value=False,
        help="Check if this is a discussion topic instead of a regular assignment"
    )

assignment_name = st.text_input(
    f"{'Discussion' if is_discussion else 'Assignment'} Name (for your reference):",
    placeholder="e.g., Week 4 Discussion or Module 3 Essay",
    help="Optional: This will be saved for quick access later"
)

# Auto-adjust scoring based on discussion checkbox
if is_discussion:
    if 'is_discussion' not in st.session_state or not st.session_state.get('is_discussion'):
        st.session_state.is_discussion = True
        st.session_state.threshold_score = 34.0
        st.session_state.total_possible_points = 50.0
        st.info("📊 Scoring adjusted for discussion: 50 points, threshold 34/50")
else:
    if st.session_state.get('is_discussion'):
        st.session_state.is_discussion = False
        st.session_state.threshold_score = 8.0
        st.session_state.total_possible_points = 12.0
        st.info("📊 Scoring adjusted for assignment: 12 points, threshold 8/12")

# ═══════════════════════════════════════════════════════════════════════════
# Step 3: Load Rubric (UPDATED TO HANDLE DISCUSSIONS)
# ═══════════════════════════════════════════════════════════════════════════
st.subheader("Step 3: Load Rubric")

item_type = "Discussion" if st.session_state.is_discussion else "Assignment"

# Optional: the must-hit items for this assignment. Each Canvas ID gets its own box, so text
# typed for one assignment never leaks into another. Used to make feedback specific.
required_elements_text = st.text_area(
    "Required elements (optional): what a complete answer must include",
    key=f"required_elements_{assignment_id or 'none'}",
    height=170,
    placeholder=(
        "One line per question, for example:\n"
        "Q1: machine bosses; immigrant hiring; jobs-for-votes exchange; election-day loyalty\n"
        "Q2: tariff; gold standard vs. greenbacks; deflation; debt spiral; free silver 16:1"
    ),
    help=(
        "Names, terms, and ideas from the lesson that a complete answer should hit. "
        "When filled in, feedback names what is missing instead of giving generic advice. "
        "Leave blank to let the model work it out from the rubric and course documents."
    ),
)

# Per-assignment leniency. Starts at the default from config_rubric.ini; changing it here
# affects only this run and never writes back to the config file.
config_leniency = config.get_leniency_multiplier()
leniency_input = st.number_input(
    f"Leniency multiplier (config default: {config_leniency:.2f})",
    min_value=0.5,
    max_value=2.0,
    value=float(config_leniency),
    step=0.05,
    format="%.2f",
    key=f"leniency_{assignment_id or 'none'}",
    help=(
        "1.00 = no adjustment, 1.20 = 20% boost, 0.90 = 10% stricter. Scores are multiplied by "
        "this value and capped at the maximum. Changing it here applies to this assignment only "
        "and does not change config_rubric.ini."
    ),
)

if assignment_id and st.button(f"Load {item_type} Rubric", type="primary"):
    with st.spinner(f"Loading {item_type.lower()} rubric and course documents..."):
        try:
            # Use the value from the box above (which started at the config default)
            leniency_multiplier = float(leniency_input)

            handler = RubricAssignmentHandler(
                assignment_key=f"dynamic_{assignment_id}",
                display_name=assignment_name or f"{item_type} {assignment_id}",
                canvas_assignment_id=assignment_id,
                course_id=selected_course_id,
                course_documents_path=documents_path,
                llm=llm,
                leniency_multiplier=leniency_multiplier,
                required_elements=required_elements_text,
            )
            st.session_state.assignment_handler = handler
            
            # Display leniency setting
            if leniency_multiplier != 1.0:
                boost_pct = (leniency_multiplier - 1.0) * 100
                st.info(f"📊 Grading leniency: {boost_pct:+.0f}% (multiplier: {leniency_multiplier})")

            # Fetch rubric using the enhanced function with is_discussion flag
            rubric_items, points_possible = fetch_assignment_or_discussion_rubric(
                api_base=canvas_url,
                token=token,
                course_id=int(selected_course_id),
                item_id=int(assignment_id),
                is_discussion=st.session_state.is_discussion
            )
            
            if rubric_items is not None:
                st.session_state.rubric = rubric_items
                
                # Use points_possible from Canvas if available, otherwise sum rubric criteria
                if points_possible is not None:
                    rubric_points = points_possible
                    st.info(f"Using Canvas assignment points: {rubric_points}")
                else:
                    rubric_points = float(sum((c.get("points") or 0) for c in rubric_items))
                    st.warning(f"Could not get points_possible from Canvas, calculated from rubric: {rubric_points}")
                
                st.session_state.rubric_total_points = rubric_points
                
                # CRITICAL: Override handler's rubric data with the correctly fetched rubric
                # The handler may have failed to load the rubric (e.g., for discussions),
                # so we need to manually set its rubric_criteria, rubric_data, and total_points
                handler.rubric_criteria = rubric_items
                handler.total_points = rubric_points
                
                # Also update rubric_data dict for any code that references it
                handler.rubric_data = {
                    "rubric_criteria": rubric_items,
                    "total_points": rubric_points
                }
                
                # Scale the rubric criteria if needed
                rubric_sum = sum(float(c.get("points", 0)) for c in rubric_items)
                if rubric_sum > 0 and abs(rubric_sum - rubric_points) > 0.01:
                    scale_factor = rubric_points / rubric_sum
                    st.info(f"Scaling rubric criteria: {rubric_sum} points → {rubric_points} points (factor: {scale_factor:.3f})")
                    
                    for criterion in handler.rubric_criteria:
                        original_points = float(criterion.get("points", 0))
                        criterion["points"] = original_points * scale_factor
                        
                        for rating in criterion.get("ratings", []):
                            original_rating_points = float(rating.get("points", 0))
                            rating["points"] = original_rating_points * scale_factor
                
                st.info(f"✓ Handler configured with {len(handler.rubric_criteria)} criteria, {handler.total_points} points")
                
                # Override total_possible_points with actual rubric points
                st.session_state.total_possible_points = rubric_points
                # Set threshold to 2/3 of total for assignments, keep custom for discussions
                if not st.session_state.is_discussion:
                    st.session_state.threshold_score = rubric_points * 0.67
                
                st.session_state.rubric_loaded_for = (int(selected_course_id), int(assignment_id))
                st.success(f"{item_type} rubric loaded successfully! ({len(rubric_items)} criteria, {rubric_points} points)")
            else:
                st.warning(f"No rubric attached to this {item_type.lower()} in Canvas.")
                st.session_state.rubric = None
                st.session_state.rubric_total_points = 0.0
                st.session_state.rubric_loaded_for = None

            if assignment_name:
                config.save_assignment_to_history(assignment_name, assignment_id, selected_course_id)

            _recompute_ready_flag()
        except Exception as e:
            st.error(f"Error loading {item_type.lower()}: {e}")
            logger.error(f"{item_type} loading error: {e}", exc_info=True)

# ==== STEP 4: Load Submissions ====
st.subheader("Step 4: Load Submissions")

if assignment_id and st.button("Download submissions from Canvas"):
    item_type = "discussion" if is_discussion else "assignment"
    
    with st.spinner(f"Clearing submissions folder and downloading {item_type}..."):
        course_id = int(selected_course_id)
        item_id = int(assignment_id)
        
        try:
            # Use different download function based on type
            if is_discussion:
                from canvas_submissions import download_discussion_submissions
                students, files, metadata = download_discussion_submissions(
                    canvas_base_url=canvas_url,
                    token=token,
                    course_id=course_id,
                    discussion_id=item_id,
                    dest_dir="./submissions",
                    clean_dest=True
                )
            else:
                from canvas_submissions import download_submissions_flat
                students, files, metadata = download_submissions_flat(
                    canvas_base_url=canvas_url,
                    token=token,
                    course_id=course_id,
                    assignment_id=item_id,
                    dest_dir="./submissions",
                    clean_dest=True
                )
            
            st.session_state.submission_metadata = metadata
            st.success(f"✓ Cleared old files and pulled {files} new files for {students} students")
            st.info("✓ Captured existing grades and feedback for preservation")
            import glob
            # Look for both .txt and .html files (assignments and discussions)
            txt_files = glob.glob("./submissions/*.txt")
            html_files = glob.glob("./submissions/*.html")
            submission_files = txt_files + html_files
            entryList = []
            student_metadata = {}
            for file in submission_files:
                basename = os.path.basename(file)
                label = os.path.splitext(basename)[0]
                with open(file, encoding='utf-8', errors='ignore') as f:
                    submission_text = f.read().strip()
                user_id = extract_user_id_from_label(label)
                if user_id and str(user_id) in metadata:
                    meta = metadata[str(user_id)]
                    current_grade = meta.get('current_grade') or meta.get('current_score', '')
                    current_feedback = meta.get('current_feedback', '')
                    name = meta.get('name', label)
                    submission_date = meta.get('submission_date', '')
                    student_metadata[str(user_id)] = {
                        'name': name,
                        'current_grade': current_grade,
                        'current_feedback': current_feedback,
                        'submission_date': submission_date
                    }
                    if current_feedback:
                        feedback_entry = current_feedback
                    else:
                        feedback_entry = f"{current_grade}/{st.session_state.rubric_total_points if st.session_state.rubric else '?'}" if current_grade else ""
                else:
                    feedback_entry = ""
                # Add score column (initially empty, will be filled during grading)
                entryList.append([label, submission_text, "", feedback_entry])
            st.session_state.entryList = entryList
            st.session_state.student_metadata = student_metadata
            st.session_state.submissions_ready_for = (int(selected_course_id), int(assignment_id))
            # Store original entries for selective grading
            st.session_state.original_entries = {}
            for entry in entryList:
                user_id = extract_user_id_from_label(entry[0])
                if user_id:
                    st.session_state.original_entries[str(user_id)] = entry.copy()
            _recompute_ready_flag()
            if entryList:
                st.success(f"✓ Loaded {len(entryList)} submissions")
            
        except Exception as e:
            st.error(f"Error downloading {item_type}: {str(e)}")
            st.error("Please check the ID and try again")

# Step 3.5: Assignment Overview
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

    item_type_display = "Discussion" if st.session_state.is_discussion else "Assignment"
    st.subheader(f"{item_type_display} Overview")
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


# Debug: Show status if not ready to grade
if not st.session_state.ready_to_grade:
    with st.expander("🔍 Debug: Why can't I grade yet?"):
        st.write("**Status Check:**")
        st.write(f"- Rubric loaded: {st.session_state.rubric is not None}")
        st.write(f"- Submissions loaded: {st.session_state.submissions_ready_for is not None}")
        st.write(f"- Rubric loaded for: {st.session_state.rubric_loaded_for}")
        st.write(f"- Submissions loaded for: {st.session_state.submissions_ready_for}")
        st.write(f"- IDs match: {st.session_state.rubric_loaded_for == st.session_state.submissions_ready_for}")
        st.write(f"- Entry count: {len(st.session_state.entryList)} submissions")
        st.write(f"- Ready to grade: {st.session_state.ready_to_grade}")
        
        if st.session_state.rubric_loaded_for != st.session_state.submissions_ready_for:
            st.warning("⚠️ The rubric and submissions are for different assignments. Make sure to load both for the same assignment ID.")
        if not st.session_state.entryList:
            st.warning("⚠️ No submission files found. Check that files were downloaded to ./submissions/")

# Step 4.5: Grading Mode Selection
if st.session_state.ready_to_grade:
    st.subheader("Step 4.5: Select Grading Mode")
    
    grading_mode = st.radio(
        "How would you like to grade?",
        ["Grade all submissions", "Select specific students to grade"],
        index=0 if st.session_state.grading_mode == "all" else 1,
        help="Choose whether to grade all submissions or select specific students"
    )
    
    st.session_state.grading_mode = "all" if grading_mode == "Grade all submissions" else "selective"
    
    # Show student selection UI if selective mode
    if st.session_state.grading_mode == "selective":
        st.markdown("---")
        st.markdown("### Select Students to Grade")
        st.info("💡 **Tip:** Unselected students will keep their existing grades and feedback. Only selected students will be re-graded.")
        
        # Build selection data
        selection_data = []
        for entry in st.session_state.entryList:
            student_label = entry[0]
            user_id = extract_user_id_from_label(student_label)
            
            if user_id and str(user_id) in st.session_state.student_metadata:
                meta = st.session_state.student_metadata[str(user_id)]
                current_grade = meta.get('current_grade') or meta.get('current_score')
                if current_grade is None:
                    current_grade = "Not graded"
                elif isinstance(current_grade, float):
                    current_grade = f"{current_grade:.1f}"
                
                # Check if there's existing feedback
                has_feedback = bool(meta.get('current_feedback'))
                grade_display = f"{current_grade} {'📝' if has_feedback else ''}"
                
                selection_data.append({
                    'user_id': str(user_id),
                    'Student': meta.get('name', student_label),
                    'Current Grade': grade_display,
                    'Submitted': meta.get('submission_date', 'N/A')
                })
            else:
                selection_data.append({
                    'user_id': str(user_id) if user_id else 'unknown',
                    'Student': student_label,
                    'Current Grade': 'Unknown',
                    'Submitted': 'N/A'
                })
        
        if selection_data:
            st.caption("📝 = Has existing feedback that will be preserved if not re-graded")
            
            # Create checkboxes for each student
            cols = st.columns([1, 4, 2, 2])
            with cols[0]: st.markdown("**Select**")
            with cols[1]: st.markdown("**Student**")
            with cols[2]: st.markdown("**Current Grade**")
            with cols[3]: st.markdown("**Submitted**")
            
            # Add "Select All" / "Deselect All" buttons
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("Select All"):
                    st.session_state.selected_students = {s['user_id'] for s in selection_data}
                    st.rerun()
            with col_b:
                if st.button("Deselect All"):
                    st.session_state.selected_students = set()
                    st.rerun()
            
            st.markdown("---")
            
            for student_info in selection_data:
                cols = st.columns([1, 4, 2, 2])
                user_id = student_info['user_id']
                
                with cols[0]:
                    is_checked = user_id in st.session_state.selected_students
                    if st.checkbox("", value=is_checked, key=f"select_{user_id}", label_visibility="collapsed"):
                        st.session_state.selected_students.add(user_id)
                    else:
                        st.session_state.selected_students.discard(user_id)
                
                with cols[1]: st.write(student_info['Student'])
                with cols[2]: st.write(student_info['Current Grade'])
                with cols[3]: st.write(student_info['Submitted'][:10] if student_info['Submitted'] != 'N/A' else 'N/A')
            
            selected_count = len(st.session_state.selected_students)
            if selected_count > 0:
                st.success(f"✓ {selected_count} student(s) selected for grading")
            else:
                st.warning("⚠ No students selected. Please select at least one student to grade.")
        else:
            st.error("No submission data available for selection")

# Step 5: Grade Submissions
if st.session_state.ready_to_grade:
    st.subheader("Step 5: Grade Submissions")
    entryList = st.session_state.entryList
    handler = st.session_state.assignment_handler
    total_points = st.session_state.rubric_total_points or handler.total_points

    # Filter entries based on grading mode
    if st.session_state.grading_mode == "selective":
        if not st.session_state.selected_students:
            st.warning("⚠ Please select at least one student to grade in Step 4.5")
        else:
            entries_to_grade = [
                entry for entry in entryList 
                if extract_user_id_from_label(entry[0]) and 
                str(extract_user_id_from_label(entry[0])) in st.session_state.selected_students
            ]
            st.info(f"Ready to grade {len(entries_to_grade)} selected submissions (out of {len(entryList)} total) using **{provider} → {selected_model}**")

            st.session_state.keep_highest_score = st.toggle(
                "Keep Highest Score?",
                value=st.session_state.keep_highest_score,
                help="If a resubmission scores lower than the student's current grade, the original score is retained and a note added to the feedback."
            )
            
            if st.button("Start Grading Selected", type="primary"):
                run_grading_process(handler, entries_to_grade, selected_model, total_points, mode="selective")
    else:
        st.info(f"Ready to grade {len(entryList)} submissions using **{provider} → {selected_model}**")
        
        if st.button("Start Grading All", type="primary"):
            run_grading_process(handler, entryList, selected_model, total_points, mode="all")

# ═══════════════════════════════════════════════════════════════════════════
# Step 6: Upload Grades & Comments to Canvas
# ═══════════════════════════════════════════════════════════════════════════
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
        # For discussions, we need to get the underlying assignment ID
        upload_assignment_id = assignment_id
        
        if st.session_state.is_discussion:
            with st.spinner("Getting discussion assignment ID..."):
                try:
                    # Fetch discussion to get the underlying assignment_id
                    discussion_url = f"{canvas_url}/courses/{selected_course_id}/discussion_topics/{assignment_id}"
                    headers = {"Authorization": f"Bearer {token}"}
                    response = requests.get(discussion_url, headers=headers, params={"include[]": "assignment"})
                    response.raise_for_status()
                    discussion_data = response.json()
                    
                    # Get the assignment object
                    assignment = discussion_data.get("assignment")
                    if not assignment:
                        st.error("This discussion is not graded (no associated assignment)")
                        st.stop()
                    
                    upload_assignment_id = assignment.get("id")
                    if not upload_assignment_id:
                        st.error("Could not find assignment ID for this discussion")
                        st.stop()
                    
                    st.info(f"Discussion ID {assignment_id} → Assignment ID {upload_assignment_id}")
                except Exception as e:
                    st.error(f"Error fetching discussion assignment ID: {e}")
                    st.stop()
        
        with st.spinner("Uploading grades..."):
            # Only upload students graded in THIS session — never re-post old comments
            session_ids = st.session_state.get("session_graded_ids", set())
            if session_ids:
                upload_list = [
                    e for e in st.session_state.entryList
                    if str(extract_user_id_from_label(e[0])) in session_ids
                ]
            else:
                upload_list = st.session_state.entryList
            ok, fail, failures = upload_all_from_entrylist(
                api_base=canvas_url,
                token=token,
                course_id=int(selected_course_id),
                assignment_id=int(upload_assignment_id),
                entry_list=upload_list,
            )
        st.success(f"Uploaded {ok} grades ({len(upload_list)} students graded this session)")
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
        
        # For discussions, we need to get the underlying assignment ID
        upload_assignment_id = assignment_id
        
        if st.session_state.is_discussion:
            with st.spinner("Getting discussion assignment ID..."):
                try:
                    # Fetch discussion to get the underlying assignment_id
                    discussion_url = f"{canvas_url}/courses/{selected_course_id}/discussion_topics/{assignment_id}"
                    headers = {"Authorization": f"Bearer {token}"}
                    response = requests.get(discussion_url, headers=headers, params={"include[]": "assignment"})
                    response.raise_for_status()
                    discussion_data = response.json()
                    
                    # Get the assignment object
                    assignment = discussion_data.get("assignment")
                    if not assignment:
                        st.error("This discussion is not graded (no associated assignment)")
                        st.stop()
                    
                    upload_assignment_id = assignment.get("id")
                    if not upload_assignment_id:
                        st.error("Could not find assignment ID for this discussion")
                        st.stop()
                    
                    st.info(f"Discussion ID {assignment_id} → Assignment ID {upload_assignment_id}")
                except Exception as e:
                    st.error(f"Error fetching discussion assignment ID: {e}")
                    st.stop()
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(up.read())
            tmp_path = tmp.name
        with st.spinner("Uploading from XLSX..."):
            ok, fail, failures = upload_all_from_xlsx(
                api_base=canvas_url,
                token=token,
                course_id=int(selected_course_id),
                assignment_id=int(upload_assignment_id),
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
        item_type = "Discussion" if st.session_state.is_discussion else "Assignment"
        st.write(f"**Type:** {item_type}")
        st.write(f"**Name:** {handler.display_name}")
        st.write(f"**Canvas ID:** {handler.canvas_assignment_id}")
        st.write(f"**Total Points:** {st.session_state.rubric_total_points or handler.total_points}")

    if st.session_state.entryList:
        st.write(f"**Submissions:** {len(st.session_state.entryList)} loaded")

    st.markdown("---")
    st.header("How to Find Canvas ID")
    st.markdown("""
    **For Assignments:**
    1. Go to your Canvas course  
    2. Click on "Assignments"  
    3. Click on the specific assignment  
    4. Look at the URL: `.../assignments/[ID]`
    
    **For Discussions:**
    1. Go to your Canvas course
    2. Click on "Discussions"
    3. Click on the discussion
    4. Look at the URL: `.../discussion_topics/[ID]`
    
    The ID is the number at the end of the URL.
    """)

    st.markdown("---")
    st.header("System Status")
    st.success("Canvas API: Connected")
    st.success(f"LLM: {provider.title()} ready")