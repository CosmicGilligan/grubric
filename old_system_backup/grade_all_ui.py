import streamlit as st
import grade_all
import csv
import os
import time
import anthropic
from create_xlsx import create_xlsx
from fixed_batch_grader_v2 import FixedBatchGraderV2
from simple_individual_grader import SimpleIndividualGrader
import claude_client
import re
from config_manager import get_config
from assignment_manager import AssignmentManager

# Load configuration and assignment manager
config = get_config()
assignment_manager = AssignmentManager(config)

# Initialize clients
client = claude_client.get_client()
api_key = claude_client.get_key()
batch_grader = FixedBatchGraderV2(client)
individual_grader = SimpleIndividualGrader(client)

# Title for your app
st.markdown("## Professor Cosmic's Magic Grading Machine")

# Configuration validation and status
config_messages = config.validate_config()
handler_messages = assignment_manager.validate_all_handlers()
all_messages = config_messages + handler_messages

if all_messages:
    with st.expander("⚠️ Configuration Status", expanded=len([m for m in all_messages if "Error" in m]) > 0):
        for message in all_messages:
            if "Error" in message:
                st.error(message)
            elif "Warning" in message:
                st.warning(message)
            else:
                st.info(message)

# Configuration reload button
col_reload, col_edit = st.columns([1, 3])
with col_reload:
    if st.button("🔄 Reload Config"):
        config.reload_config()
        assignment_manager.reload_handlers()
        st.rerun()

with col_edit:
    st.info("💡 Edit config.ini file to add new courses or assignment types")

# Initialize session state variables
if 'stop_processing' not in st.session_state:
    st.session_state.stop_processing = False
if 'debug_batch' not in st.session_state:
    st.session_state.debug_batch = False

# Get options from config and assignment manager
courses = config.get_courses()
assignment_options = assignment_manager.get_assignment_options()

if not courses:
    st.error("❌ No courses configured. Please check your config.ini file.")
    st.stop()

if not assignment_options:
    st.error("❌ No assignment types configured. Please check your config.ini file.")
    st.stop()

# Initialize session state for selections
if 'selected_course' not in st.session_state:
    st.session_state.selected_course = courses[0] if courses else None
if 'selected_assignment_key' not in st.session_state:
    st.session_state.selected_assignment_key = assignment_options[0][1] if assignment_options else None

# Conditional to run crawl only if needed
if 'US1List' not in st.session_state or 'US2List' not in st.session_state:
    with st.spinner("Initializing data..."):
        grade_all.crawl()        
        st.session_state.US1List = grade_all.US1List 
        st.session_state.US2List = grade_all.US2List
        st.session_state.US1DList = grade_all.US1DList
        st.session_state.US2DList = grade_all.US2DList

def clean_feedback_comprehensive(feedback_text):
    """Clean greetings and closings from feedback text."""
    if not feedback_text or not isinstance(feedback_text, str):
        return feedback_text
    
    import re
    
    # Handle SCORE: prefix
    score_prefix = ""
    score_match = re.match(r'^(SCORE:\s*\d+/\d+\s*)', feedback_text, re.IGNORECASE)
    if score_match:
        score_prefix = score_match.group(1)
        feedback_text = feedback_text[len(score_prefix):].strip()
    
    # Remove greeting patterns
    greeting_patterns = [
        r'^Dear\s+[^,:\n.]*,?\s*',
        r'^Hello\s+[^,:\n.]*,?\s*',
        r'^Hi\s+[^,:\n.]*,?\s*',
        r'^Thank\s+you\s+for\s+submitting[^.]*\.\s*',
        r'^Thank\s+you\s+for\s+your\s+[^.]*\.\s*',
        r'^What\s+an?\s+excellent[^!]*!\s*',
        r'^Greetings\s*[,:]?\s*',
        r'^Good\s+\w+\s*[,:]?\s*',
        r'^To\s+[^,:\n.]*[,:]?\s*',
    ]
    
    for pattern in greeting_patterns:
        feedback_text = re.sub(pattern, '', feedback_text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Remove closing patterns
    closing_patterns = [
        r'Best\s+regards.*$',
        r'Sincerely.*$', 
        r'Best\s+wishes.*$',
        r'Thank\s+you.*$',
        r'Yours\s+truly.*$',
        r'\[Professor[^\]]*\].*$',
        r'Dr\.\s+\w+.*$',
        r'Professor\s+\w+.*$',
    ]
    
    for pattern in closing_patterns:
        feedback_text = re.sub(pattern, '', feedback_text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Clean up whitespace
    feedback_text = re.sub(r'\n\s*\n', '\n', feedback_text)
    feedback_text = re.sub(r'^\s+', '', feedback_text).strip()
    feedback_text = re.sub(r'\s+', ' ', feedback_text)
    
    # Ensure proper capitalization
    if feedback_text:
        feedback_text = feedback_text[0].upper() + feedback_text[1:] if len(feedback_text) > 1 else feedback_text.upper()
    
    # Restore score prefix
    if score_prefix:
        feedback_text = score_prefix + " " + feedback_text
    
    return feedback_text

def initialize_entry_list_with_crn(crn):
    """Initialize entryList using explicit CRN"""
    print(f"DEBUG: Loading submissions for CRN {crn}")
    return grade_all.makeEntryList(crn)

# Course/Assignment Selection
st.subheader("Course Selection")
col1, col2 = st.columns(2)

with col1:
    # Course Selection
    course_options = [(f"{name} (CRN: {crn})", (name, crn)) for name, crn in courses]
    
    selected_course_index = 0
    if st.session_state.selected_course:
        for i, (_, course_data) in enumerate(course_options):
            if course_data == st.session_state.selected_course:
                selected_course_index = i
                break
    
    selected_course_display = st.selectbox(
        "Select Course Section:",
        options=[display for display, _ in course_options],
        index=selected_course_index,
        help="Select the specific course section to load submissions from"
    )
    
    # Get the actual course data
    for display, course_data in course_options:
        if display == selected_course_display:
            st.session_state.selected_course = course_data
            break
    
    selected_course_name, selected_crn = st.session_state.selected_course

with col2:
    # Assignment Type Selection using assignment manager
    assignment_display_options = [display_name for display_name, _ in assignment_options]
    
    selected_assignment_index = 0
    current_handler = assignment_manager.get_handler(st.session_state.selected_assignment_key)
    if current_handler:
        for i, (display_name, key) in enumerate(assignment_options):
            if key == st.session_state.selected_assignment_key:
                selected_assignment_index = i
                break
    
    selected_assignment_display = st.selectbox(
        "Assignment Type:",
        options=assignment_display_options,
        index=selected_assignment_index,
        help="Select the type of assignment to grade"
    )
    
    # Get the selected assignment key
    for display_name, key in assignment_options:
        if display_name == selected_assignment_display:
            st.session_state.selected_assignment_key = key
            break

# Get current assignment handler
current_handler = assignment_manager.get_handler(st.session_state.selected_assignment_key)
if not current_handler:
    st.error(f"❌ No handler found for assignment type: {st.session_state.selected_assignment_key}")
    st.stop()

# Show current selection
st.info(f"📚 Selected: {selected_course_name} - {current_handler.display_name}")

# Module/Topic Selection (if required by assignment type)
moduleList = None
course_number = config.get_course_number_from_name(selected_course_name)

if current_handler.requires_module_selection():
    if course_number:
        available_modules = current_handler.get_available_modules(course_number)
        
        if available_modules:
            moduleList = st.selectbox(
                f"Select {current_handler.display_name} Module/Topic:", 
                options=available_modules,
                help=f"Choose the specific module/topic to grade"
            )
        else:
            st.warning(f"No modules available for {current_handler.display_name}. Check configuration.")
            moduleList = None
    else:
        st.error("Could not determine course number from course name")
        moduleList = None

# Initialize or update entryList when CRN changes
if ('entryList' not in st.session_state or 
    'last_selected_crn' not in st.session_state or 
    st.session_state.last_selected_crn != selected_crn):
    
    with st.spinner(f"Loading submissions for {selected_course_name}..."):
        st.session_state.entryList = initialize_entry_list_with_crn(selected_crn)
        st.session_state.last_selected_crn = selected_crn
        
        if st.session_state.entryList:
            st.success(f"✅ Loaded {len(st.session_state.entryList)} submissions from {selected_course_name}")
        else:
            st.warning(f"⚠️ No submissions found for {selected_course_name}")

entryList = st.session_state.entryList

# Load New Submissions Button
if st.button("🔄 Load New Submissions", type="secondary"):
    st.session_state.entryList = []
    
    st.markdown(f"Loading {current_handler.display_name} submissions from {selected_course_name}...")
    st.info(f"📡 Using CRN: {selected_crn}")

    with st.spinner(f"Loading submissions from Canvas (CRN: {selected_crn})..."):
        st.session_state.entryList = initialize_entry_list_with_crn(selected_crn)
        entryList = st.session_state.entryList
        st.session_state.last_selected_crn = selected_crn
    
    if entryList:
        st.success(f"✅ Loaded {len(entryList)} submissions from {selected_course_name}")
        # Removed balloons from here - they should only appear after grading
    else:
        st.error(f"❌ No submissions found for CRN {selected_crn}")

# Grading configuration section
st.subheader("Grading Configuration")
col3, col4, col5 = st.columns(3)

with col3:
    # Processing method from config with fallback
    default_method = config.get_setting('default_processing_method', 'Individual (Reliable)')
    processing_method = st.radio(
        "Processing Method",
        ["Individual (Reliable)", "Batch (Faster)"],
        index=0 if default_method == "Individual (Reliable)" else 1,
        help="Individual: Most reliable, one student at a time. Batch: Faster, multiple students per API call."
    )

with col4:
    # Scoring scale automatically determined from assignment handler
    scoring_scale = current_handler.default_points
    
    st.info(f"**Scoring Scale:** {scoring_scale} points")
    st.caption(f"Fixed scale for {current_handler.display_name}")

with col5:
    # Model selection from config
    default_model = config.get_setting('default_model', 'claude-3-5-sonnet-20241022')
    model_choice = st.selectbox(
        "Claude Model",
        ["claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-haiku-20240307"],
        index=0,  # Default to Sonnet
        help="Sonnet: Balanced speed/quality, Opus: Highest quality, Haiku: Fastest"
    )

def do_some_work_optimized():
    """Optimized grading function using assignment handlers"""
    
    # Reset stop flag
    st.session_state.stop_processing = False
    
    # Get grading data from assignment handler
    try:
        module_number = None
        if current_handler.requires_module_selection():
            if not moduleList:
                st.error(f"Module selection required for {current_handler.display_name}")
                return
            module_number = int(moduleList[-3:])
        
        grading_data = assignment_manager.get_grading_data(
            st.session_state.selected_assignment_key, 
            course_number, 
            module_number
        )
        
    except Exception as e:
        st.error(f"Error getting grading data: {e}")
        return
    
    max_score = grading_data['max_score']
    lecture_content = grading_data['lecture_content']
    questions = grading_data['questions']
    use_batch = processing_method == "Batch (Faster)"
    use_individual = processing_method == "Individual (Reliable)"
    
    # Prepare submissions for processing
    submissions = [(entry[0], entry[1]) for entry in entryList]
    
    if not submissions:
        st.error("No student submissions found")
        return
    
    # Show processing info
    method_name = "individual" if use_individual else "batch"
    st.info(f"🎯 Grading {len(submissions)} {current_handler.display_name}s using {max_score}-point scale ({method_name} processing)")
    st.info(f"📚 Course: {selected_course_name} | Assignment: {current_handler.display_name}")
    
    mapped_course = grading_data['mapped_course_number']
    if mapped_course != course_number:
        st.info(f"🔄 Using course mapping: {course_number} → {mapped_course}")
    
    # Progress tracking elements
    progress_text = st.empty()
    progress_bar = st.progress(0)
    current_student = st.empty()
    
    # Stop button container
    stop_container = st.empty()
    
    # Show stop button during processing
    with stop_container.container():
        if st.button("🛑 Stop Processing", key="stop_btn", type="secondary"):
            st.session_state.stop_processing = True
            st.warning("⏹️ Stop requested - finishing current item...")
    
    # Define progress callback functions
    def update_progress(progress_value):
        """Update the progress bar"""
        progress_bar.progress(progress_value)
    
    def update_status(status_text):
        """Update the status text"""
        progress_text.text(status_text)
        # Extract current student name if available
        if "Grading" in status_text and "(" in status_text:
            # Extract student name from status like "Grading John Doe (2/10)"
            student_part = status_text.split("Grading")[1].split("(")[0].strip()
            current_student.info(f"🎓 Currently grading: **{student_part}**")
    
    try:
        if use_batch:
            # Use batch grading with progress callbacks
            if st.session_state.debug_batch:
                st.info("🔍 Debug mode enabled - check console/terminal for detailed output")
            
            progress_text.text("Starting batch processing...")
            results = batch_grader.grade_all_submissions(
                submissions, lecture_content, questions, max_score,
                progress_callback=update_progress,
                status_callback=update_status
            )
        else:
            # Use individual grading with progress callbacks
            progress_text.text("Starting individual processing...")
            results = individual_grader.grade_all_submissions(
                lecture_content, 
                questions, 
                submissions, 
                max_score,
                model_choice,
                progress_callback=update_progress,
                status_callback=update_status
            )
    
    except Exception as e:
        st.error(f"Error during grading: {e}")
        return

    # Clear progress elements
    current_student.empty()
    stop_container.empty()
    
    # Check if processing was stopped
    if st.session_state.stop_processing:
        progress_text.text("Processing stopped by user")
        st.warning(f"⏹️ Processing stopped! Graded {len([r for r in results if r.get('letter_grade') != 'STOPPED'])} of {len(submissions)} submissions.")
        # Reset stop flag
        st.session_state.stop_processing = False
    else:
        progress_text.text("Grading completed!")
        progress_bar.progress(1.0)
    
    # Update entryList with results
    for i, result in enumerate(results):
        if i < len(entryList):
            # Format the result for your existing CSV structure
            score_info = f"{result['score']}/{result['max_score']} ({result['letter_grade']})"
            grade_feedback = f"{score_info} - {result['feedback']}"
            entryList[i][2] = grade_feedback
    
    # Save results
    submissions_dir = config.get_path('submissions_directory', './submissions/')
    os.makedirs(submissions_dir, exist_ok=True)
    filename = os.path.join(submissions_dir, 'completions.csv')
    
    with open(filename, 'w+', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(entryList)
    
    time.sleep(0.5)
    create_xlsx(filename)
    
    # Try to open with configured or default application
    try:
        os.system(f"wps {filename.replace('.csv', '.xlsx')} &")
    except:
        st.info(f"Results saved to {filename}")
    
    progress_text.text("✅ All processing completed!")
    progress_bar.progress(1.0)
    
    # Show summary stats
    if results:
        completed_results = [r for r in results if r.get('letter_grade') != 'STOPPED']
        if completed_results:
            avg_score = sum(r['score'] for r in completed_results) / len(completed_results)
            grade_distribution = {}
            for r in completed_results:
                grade = r['letter_grade']
                grade_distribution[grade] = grade_distribution.get(grade, 0) + 1
            
            completion_status = "✅ Completed!" if len(completed_results) == len(results) else f"⏹️ Partially completed ({len(completed_results)}/{len(results)})"
            st.success(f"{completion_status} Average score: {avg_score:.1f}/{max_score}")
            
            # Show grade distribution
            col_a, col_b, col_c, col_d, col_f = st.columns(5)
            with col_a:
                st.metric("A's", grade_distribution.get('A', 0))
            with col_b:
                st.metric("B's", grade_distribution.get('B', 0))
            with col_c:
                st.metric("C's", grade_distribution.get('C', 0))
            with col_d:
                st.metric("D's", grade_distribution.get('D', 0))
            with col_f:
                st.metric("F's", grade_distribution.get('F', 0))

def do_some_work_individual():
    """Individual grading function using assignment handlers"""
    
    try:
        module_number = None
        if current_handler.requires_module_selection():
            if not moduleList:
                st.error(f"Module selection required for {current_handler.display_name}")
                return
            module_number = int(moduleList[-3:])
        
        grading_data = assignment_manager.get_grading_data(
            st.session_state.selected_assignment_key, 
            course_number, 
            module_number
        )
        
    except Exception as e:
        st.error(f"Error getting grading data: {e}")
        return
    
    promptStr = grading_data['prompt_string']
    max_score = grading_data['max_score']
    
    i = 0
    total_steps = len(entryList)
    if total_steps <= 0:
        st.error("No student submissions found")
        return
    iPctStep = 1/total_steps

    for x in entryList:
        with st.spinner(f"Grading submission {i+1}/{total_steps}..."):
            progress_bar.progress(i * iPctStep)
            i += 1
            responseStr = x[1]

            try:
                message = client.messages.create(
                    model=model_choice,  # Use configured model
                    max_tokens=4000,
                    system=f"Grade this submission out of {max_score} points. Start your response with 'SCORE: X/{max_score}' then provide detailed feedback.",
                    messages=[
                        {"role": "user", "content": promptStr + "\n\nSTUDENT SUBMISSION:\n" + responseStr}
                    ]
                )
                
                raw_result = message.content[0].text.replace("\n", " ") + "\n"
                
                # APPLY CLEANING HERE
                cleaned_result = clean_feedback_comprehensive(raw_result)
                x[2] = cleaned_result
                
            except Exception as e:
                st.error(f"Error grading submission: {str(e)}")
                x[2] = f"Error: {str(e)}\n"
           
    # Save results
    submissions_dir = config.get_path('submissions_directory', './submissions/')
    os.makedirs(submissions_dir, exist_ok=True)
    filename = os.path.join(submissions_dir, 'completions.csv')
    
    with open(filename, 'w+', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(entryList)
    
    time.sleep(0.5)
    create_xlsx(filename)
    
    try:
        os.system(f"wps {filename.replace('.csv', '.xlsx')} &")
    except:
        st.info(f"Results saved to {filename}")

# Button to start grading
if st.button("Grade Submissions", type="primary"):
    if not entryList:
        st.error("No submissions loaded. Please click 'Load New Submissions' first.")
    elif current_handler.requires_module_selection() and not moduleList:
        st.error(f"Please select a module for {current_handler.display_name}.")
    else:
        do_some_work_optimized()
        st.balloons()

# Sidebar with additional options
with st.sidebar:
    st.header("Settings")
    
    # Assignment handler info
    st.subheader("🎯 Current Assignment Handler")
    handler_info = assignment_manager.get_handler_info(st.session_state.selected_assignment_key)
    if handler_info:
        st.write(f"**Type:** {handler_info['assignment_key']}")
        st.write(f"**Fixed Scale:** {handler_info['default_points']} points")
        st.write(f"**Requires Module:** {'Yes' if handler_info['requires_module'] else 'No'}")
        
        if handler_info['course_mapping']:
            st.write("**Course Mapping:**")
            for source, target in handler_info['course_mapping'].items():
                st.write(f"  • {source} → {target}")
        
        # Show grading criteria if available
        if 'grading_criteria' in handler_info:
            with st.expander("📋 Grading Criteria"):
                for criterion in handler_info['grading_criteria']:
                    st.write(f"• {criterion}")
        
        # Show special instructions if available
        if 'special_instructions' in handler_info:
            with st.expander("ℹ️ Special Instructions"):
                st.write(handler_info['special_instructions'])
    
    # Scoring scale info
    st.subheader("📊 Grade Scale Reference")
    grade_scale = assignment_manager.get_grade_scale_info(st.session_state.selected_assignment_key)
    for grade, description in grade_scale.items():
        st.write(f"**{grade}:** {description}")

    # Batch size settings
    if processing_method == "Batch (Faster)":
        st.subheader("⚡ Batch Settings")
        default_batch_size = int(config.get_setting('default_batch_size', '4'))
        max_batch_size = st.slider(
            "Max submissions per batch",
            min_value=3,
            max_value=8,
            value=default_batch_size,
            help="Smaller batches are more reliable for consistent scoring."
        )
        
        default_debug = config.get_setting('default_debug_mode', 'False').lower() == 'true'
        st.session_state.debug_batch = st.checkbox(
            "Debug batch processing",
            value=st.session_state.get('debug_batch', default_debug),
            help="Show what prompt is sent to Claude and what it responds with"
        )
        
        # Update batch grader settings
        batch_grader.max_submissions_per_batch = max_batch_size

    # Enhanced debug info
    st.subheader("ℹ️ Current Selection")
    st.write(f"**Course:** {selected_course_name}")
    st.write(f"**CRN:** {selected_crn}")
    st.write(f"**Assignment:** {current_handler.display_name}")
    st.write(f"**Handler Type:** {current_handler.assignment_key}")
    if course_number:
        st.write(f"**Base Course #:** {course_number}")
        mapped_course = current_handler.get_mapped_course_number(course_number)
        if mapped_course != course_number:
            st.write(f"**Mapped Course #:** {mapped_course}")
    if moduleList:
        st.write(f"**Module:** {moduleList}")
    if entryList:
        st.write(f"**Submissions:** {len(entryList)} loaded")
        # Show first few student names
        if len(entryList) > 0:
            st.write("**Students:**")
            for i, entry in enumerate(entryList[:5]):
                st.write(f"• {entry[0]}")
            if len(entryList) > 5:
                st.write(f"• ... and {len(entryList) - 5} more")
    else:
        st.write("**Submissions:** None loaded")
        
    # Manual refresh button
    if st.button("🔄 Refresh Submissions"):
        st.session_state.entryList = initialize_entry_list_with_crn(selected_crn)
        st.session_state.last_selected_crn = selected_crn
        st.rerun()

# Display current submissions preview
if entryList:
    st.subheader("Current Submissions Preview")
    preview_df = []
    for i, entry in enumerate(entryList[:5]):  # Show first 5
        preview_df.append({
            "Student": entry[0],
            "Submission Preview": entry[1][:100] + "..." if len(entry[1]) > 100 else entry[1],
            "Status": "Graded" if entry[2] else "Pending"
        })
    
    st.dataframe(preview_df, use_container_width=True)
    
    if len(entryList) > 5:
        st.caption(f"Showing 5 of {len(entryList)} submissions")

# Footer with configuration info
st.markdown("---")
config_summary = f"💡 **Settings**: {scoring_scale}-point scale • {current_handler.display_name} • {processing_method} processing"
mapped_course = current_handler.get_mapped_course_number(course_number) if course_number else None
if mapped_course and mapped_course != course_number:
    config_summary += f" • Course mapping: {course_number}→{mapped_course}"

st.markdown(config_summary)