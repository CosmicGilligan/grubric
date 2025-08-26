"""
Dynamic Streamlit UI for rubric-based grading system
Allows entering Canvas assignment IDs per session instead of preconfiguring all assignments
"""

import streamlit as st
import pandas as pd
import os
import time
import csv
from typing import List, Dict, Tuple, Optional
import logging
from datetime import datetime
import configparser

# Import components
from canvas_rubric_api import CanvasRubricAPI, load_canvas_credentials
from course_document_processor import CourseDocumentProcessor
from rubric_assignment_handler import RubricAssignmentHandler
import anthropic
import grade_all
from create_xlsx import create_xlsx

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="Dynamic Rubric Grading",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Configuration management
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

# Initialize Anthropic client
@st.cache_resource
def get_anthropic_client():
    try:
        with open('/home/drkeithcox/anthropic.key', 'r') as f:
            api_key = f.read().strip()
        return anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        st.error(f"Failed to load Anthropic API key: {e}")
        st.stop()

client = get_anthropic_client()

# Initialize Canvas API
@st.cache_resource
def get_canvas_api():
    try:
        canvas_url, api_token = load_canvas_credentials()
        return CanvasRubricAPI(canvas_url, api_token)
    except Exception as e:
        st.error(f"Failed to initialize Canvas API: {e}")
        st.stop()

canvas_api = get_canvas_api()

# Session state initialization
if 'selected_course' not in st.session_state:
    st.session_state.selected_course = None
if 'assignment_handler' not in st.session_state:
    st.session_state.assignment_handler = None
if 'entryList' not in st.session_state:
    st.session_state.entryList = []
if 'rubric_loaded' not in st.session_state:
    st.session_state.rubric_loaded = False

def run_grading_process(handler, entryList, model, total_points):
    """Perform the grading process - renamed to avoid conflicts"""
    
    # Progress tracking
    progress_bar = st.progress(0)
    status_text = st.empty()
    current_student = st.empty()
    
    # Stop button
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
            # Check for stop
            if st.session_state.get('stop_processing', False):
                status_text.text("Processing stopped by user")
                break
            
            # Update progress
            progress = i / total
            progress_bar.progress(progress)
            status_text.text(f"Grading {student_name} ({i+1}/{total})")
            current_student.info(f"Currently grading: **{student_name}**")
            
            # Grade submission
            result = handler.grade_submission(submission_text, student_name, model)
            results.append(result)
            
            logger.info(f"Graded {student_name}: {result['score']}/{result['max_score']} ({result['letter_grade']})")
        
        # Clear progress elements
        current_student.empty()
        stop_container.empty()
        progress_bar.progress(1.0)
        status_text.text("Grading completed!")
        
        # Update entryList with results
        for i, result in enumerate(results):
            if i < len(entryList):
                feedback = f"{result['score']}/{result['max_score']} ({result['letter_grade']}) - {result['feedback']}"
                entryList[i][2] = feedback
        
        # Save results
        submissions_dir = config.get_path('submissions_directory', './submissions/')
        os.makedirs(submissions_dir, exist_ok=True)
        filename = os.path.join(submissions_dir, 'completions.csv')
        
        with open(filename, 'w+', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(entryList)
        
        create_xlsx(filename)
        
        # Show summary
        if results:
            avg_score = sum(r['score'] for r in results) / len(results)
            grade_distribution = {}
            for r in results:
                grade = r['letter_grade']
                grade_distribution[grade] = grade_distribution.get(grade, 0) + 1
            
            st.success(f"Completed! Average score: {avg_score:.1f}/{total_points}")
            
            # Grade distribution
            col_a, col_b, col_c, col_d, col_f = st.columns(5)
            with col_a: st.metric("A's", grade_distribution.get('A', 0))
            with col_b: st.metric("B's", grade_distribution.get('B', 0))
            with col_c: st.metric("C's", grade_distribution.get('C', 0))
            with col_d: st.metric("D's", grade_distribution.get('D', 0))
            with col_f: st.metric("F's", grade_distribution.get('F', 0))
        
        st.balloons()
        
    except Exception as e:
        st.error(f"Error during grading: {e}")
        logger.error(f"Grading error: {e}")

# Title
st.markdown("## Dynamic Rubric-Based Grading System")
st.markdown("*Enter Canvas assignment ID to automatically load rubric and grade submissions*")

# Step 1: Course Selection
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
    # Assignment ID input
    assignment_id = st.text_input(
        "Canvas Assignment ID:",
        help="Find this in your Canvas assignment URL: .../assignments/[ID]",
        placeholder="e.g., 123456"
    )
    
    # Optional assignment name for history
    assignment_name = st.text_input(
        "Assignment Name (for your reference):",
        placeholder="e.g., Module 3 Essay",
        help="Optional: This will be saved for quick access later"
    )

with col2:
    # Assignment history
    st.markdown("**Recent Assignments:**")
    history = config.get_assignment_history()
    
    if history:
        for name, hist_id, hist_course in history[-5:]:  # Show last 5
            if st.button(f"{name} ({hist_id})", key=f"history_{hist_id}"):
                st.session_state.assignment_id_input = hist_id
                st.session_state.assignment_name_input = name
                st.rerun()
    else:
        st.caption("No recent assignments")

# Step 3: Load Rubric
if assignment_id:
    if st.button("Load Assignment Rubric", type="primary"):
        with st.spinner("Loading rubric and course documents..."):
            try:
                # Create assignment handler
                handler = RubricAssignmentHandler(
                    assignment_key=f"dynamic_{assignment_id}",
                    display_name=assignment_name or f"Assignment {assignment_id}",
                    canvas_assignment_id=assignment_id,
                    course_id=selected_course_id,
                    course_documents_path=documents_path,
                    claude_client=client
                )
                
                st.session_state.assignment_handler = handler
                st.session_state.rubric_loaded = True
                
                # Save to history if name provided
                if assignment_name:
                    config.save_assignment_to_history(assignment_name, assignment_id, selected_course_id)
                
                st.success("Assignment loaded successfully!")
                st.rerun()
                
            except Exception as e:
                st.error(f"Error loading assignment: {e}")
                logger.error(f"Assignment loading error: {e}")

# Step 4: Show Assignment Details (if loaded)
if st.session_state.rubric_loaded and st.session_state.assignment_handler:
    handler = st.session_state.assignment_handler
    
    # Create assignment info from handler attributes
    total_points = handler.total_points
    criterion_count = len(handler.rubric_criteria)
    rubric_criteria = handler.rubric_criteria
    
    # Get document stats if available
    document_chunks = 0
    unique_documents = 0
    if handler.document_processor and handler.document_processor.df is not None:
        document_chunks = len(handler.document_processor.df)
        unique_documents = handler.document_processor.df['filename'].nunique()
    
    st.subheader("Step 3: Assignment Overview")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Points", total_points)
    with col2:
        st.metric("Rubric Criteria", criterion_count)
    with col3:
        st.metric("Course Documents", document_chunks)
    
    # Show rubric details
    if rubric_criteria:
        with st.expander("View Rubric Criteria", expanded=False):
            for i, criterion in enumerate(rubric_criteria, 1):
                st.write(f"**{i}. {criterion.get('description', 'No description')}** ({criterion.get('points', 0)} points)")
                ratings = criterion.get('ratings', [])
                if ratings:
                    st.caption(f"Rating levels: {len(ratings)}")
                st.divider()
    
    # Load submissions
    st.subheader("Step 4: Load Submissions")
    
    # Initialize or refresh submissions
    if ('entryList' not in st.session_state or 
        'last_selected_course_id' not in st.session_state or 
        st.session_state.last_selected_course_id != selected_course_id):
        
        with st.spinner(f"Loading submissions for {selected_course_name}..."):
            st.session_state.entryList = grade_all.makeEntryList(selected_course_id)
            st.session_state.last_selected_course_id = selected_course_id
            
            if st.session_state.entryList:
                st.success(f"Loaded {len(st.session_state.entryList)} submissions")
            else:
                st.warning("No submissions found")
    
    entryList = st.session_state.entryList
    
    # Refresh submissions button
    if st.button("Refresh Submissions"):
        with st.spinner("Refreshing submissions..."):
            st.session_state.entryList = grade_all.makeEntryList(selected_course_id)
            entryList = st.session_state.entryList
        st.success(f"Refreshed: {len(entryList)} submissions loaded")
        st.rerun()
    
    # Step 5: Grading
    if entryList:
        st.subheader("Step 5: Grade Submissions")
        
        col1, col2 = st.columns(2)
        with col1:
            # Model selection
            model_options = {
                "Claude 3.5 Sonnet (Recommended)": "claude-3-5-sonnet-20241022",
                "Claude 3 Opus (Most Capable)": "claude-3-opus-20240229",
                "Claude 3 Haiku (Fastest)": "claude-3-haiku-20240307"
            }
            
            selected_model_display = st.selectbox(
                "Claude Model:",
                options=list(model_options.keys()),
                help="Sonnet offers the best balance of speed and quality"
            )
            
            selected_model = model_options[selected_model_display]
        
        with col2:
            st.info(f"Ready to grade {len(entryList)} submissions")
        
        # Grading button
        if st.button("Start Grading", type="primary"):
            run_grading_process(handler, entryList, selected_model, total_points)

# Sidebar information
with st.sidebar:
    st.header("Current Session")
    
    if st.session_state.selected_course:
        course_name, course_id, _ = st.session_state.selected_course
        st.write(f"**Course:** {course_name}")
        st.write(f"**Course ID:** {course_id}")
    
    if st.session_state.rubric_loaded and st.session_state.assignment_handler:
        handler = st.session_state.assignment_handler
        st.write(f"**Assignment:** {handler.display_name}")
        st.write(f"**Canvas ID:** {handler.canvas_assignment_id}")
        st.write(f"**Total Points:** {handler.total_points}")
    
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
    
    Example URL:
    `https://school.instructure.com/courses/12345/assignments/67890`
    
    Assignment ID = **67890**
    """)
    
    st.markdown("---")
    st.header("System Status")
    st.success("Canvas API: Connected")
    st.success("Anthropic API: Connected")
    
    if st.session_state.assignment_handler:
        if st.session_state.assignment_handler.document_processor:
            st.success("Course Documents: Loaded")
        else:
            st.warning("Course Documents: Not loaded")

# Footer
st.markdown("---")
st.caption("Dynamic Rubric-Based Grading System - Enter assignment IDs on demand")