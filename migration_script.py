#!/usr/bin/env python3
"""
Migration script to help transition from the old grading system to the new rubric-based system
"""

import os
import shutil
from pathlib import Path
import argparse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def backup_old_system():
    """Backup the old system files"""
    logger.info("Creating backup of old system...")
    
    backup_dir = "old_system_backup"
    os.makedirs(backup_dir, exist_ok=True)
    
    files_to_backup = [
        "config.ini",
        "assignment_base.py", 
        "assignment_factory.py",
        "assignment_manager.py",
        "grade_all_ui.py",
        "batch_grading_system.py",
        "fixed_batch_grader_v2.py"
    ]
    
    for file in files_to_backup:
        if os.path.exists(file):
            shutil.copy2(file, backup_dir)
            logger.info(f"Backed up {file}")
    
    # Backup assignment handlers directory if it exists
    if os.path.exists("assignment_handlers"):
        shutil.copytree("assignment_handlers", os.path.join(backup_dir, "assignment_handlers"), dirs_exist_ok=True)
        logger.info("Backed up assignment_handlers directory")
    
    logger.info(f"Backup completed in {backup_dir}")

def check_prerequisites():
    """Check if all prerequisites are in place"""
    logger.info("Checking prerequisites...")
    
    issues = []
    
    # Check if ../db/text exists
    if not os.path.exists("../db/text"):
        issues.append("Course documents directory ../db/text not found")
    
    # Check Canvas secrets file
    if not os.path.exists("/home/drkeithcox/canvas-secrets.key"):
        issues.append("Canvas secrets file not found at /home/drkeithcox/canvas-secrets.key")
    
    # Check Anthropic key file
    if not os.path.exists("/home/drkeithcox/anthropic.key"):
        issues.append("Anthropic key file not found at /home/drkeithcox/anthropic.key")
    
    # Check required Python packages
    required_packages = [
        ("anthropic", "anthropic"),
        ("sentence-transformers", "sentence_transformers"), 
        ("scikit-learn", "sklearn"),  # Package name vs import name
        ("streamlit", "streamlit"),
        ("pandas", "pandas"),
        ("numpy", "numpy")
    ]
    
    for package_name, import_name in required_packages:
        try:
            __import__(import_name)
        except ImportError:
            issues.append(f"Required package not installed: {package_name}")
    
    if issues:
        logger.error("Prerequisites check failed:")
        for issue in issues:
            logger.error(f"  - {issue}")
        return False
    
    logger.info("All prerequisites satisfied")
    return True

def setup_course_directories():
    """Set up course document directories if they don't exist"""
    logger.info("Setting up course directories...")
    
    base_path = Path("../db/text")
    course_dirs = ["HIST109", "HIST110"]
    
    for course in course_dirs:
        course_path = base_path / course
        course_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created/verified course directory: {course_path}")
        
        # Create a README file in each course directory
        readme_path = course_path / "README.md"
        if not readme_path.exists():
            with open(readme_path, 'w') as f:
                f.write(f"# {course} Course Documents\n\n")
                f.write("Place course materials here:\n")
                f.write("- Lecture notes (.txt, .md files)\n")
                f.write("- PDFs (.pdf files)\n") 
                f.write("- Word documents (.docx files)\n")
                f.write("- Any other course materials\n\n")
                f.write("The system will automatically process all supported file types in this directory.\n")
            logger.info(f"Created README for {course}")

def create_example_assignment_config():
    """Create example assignment configurations"""
    logger.info("Creating example assignment configurations...")
    
    config_content = """# Example configuration for rubric-based grading system
# Update the canvas_assignment_id values with your actual Canvas assignment IDs

[COURSES]
hist109_section1 = HIST109 Section 1, 2480616
hist109_section2 = HIST109 Section 2, 2480619
hist110 = HIST110, 2486450

[RUBRIC_ASSIGNMENTS]
# Format: assignment_key = Display Name, canvas_assignment_id, course_id, documents_path
# TODO: Replace these example assignment IDs with your actual Canvas assignment IDs
module_assignment_109 = Module Assignment HIST109, REPLACE_WITH_CANVAS_ID, 2480616, ../db/text/HIST109
discussion_109 = Discussion Post HIST109, REPLACE_WITH_CANVAS_ID, 2480616, ../db/text/HIST109
final_essay_109 = Final Essay HIST109, REPLACE_WITH_CANVAS_ID, 2480616, ../db/text/HIST109

# To find Canvas assignment IDs:
# 1. Go to your Canvas course
# 2. Navigate to Assignments
# 3. Click on an assignment
# 4. Look at the URL - the assignment ID is the number at the end
# Example: https://yourschool.instructure.com/courses/12345/assignments/67890
# The assignment ID is 67890

[COURSE_DOCUMENT_PATHS]
HIST109 = ../db/text/HIST109
HIST110 = ../db/text/HIST110

[SETTINGS]
default_processing_method = Individual (Rubric-Based)
default_debug_mode = False
default_model = claude-3-5-sonnet-20241022
max_tokens_per_chunk = 500
similarity_threshold = 0.1
top_k_documents = 5
max_context_length = 2000

[API_SETTINGS]
canvas_secrets_file = /home/drkeithcox/canvas-secrets.key
anthropic_key_file = /home/drkeithcox/anthropic.key
max_api_retries = 3
api_timeout = 30

[PATHS]
submissions_directory = ./submissions/
course_documents_base = ../db/text/
embedding_cache_directory = ./embeddings_cache/
logs_directory = ./logs/

[GRADING_SCALES]
grade_a_min = 90.0
grade_b_min = 80.0
grade_c_min = 70.0
grade_d_min = 60.0

[EMBEDDING_SETTINGS]
embedding_model = all-MiniLM-L6-v2
batch_size = 32
force_refresh_embeddings = False
"""
    
    with open("config_rubric.ini", "w") as f:
        f.write(config_content)
    
    logger.info("Created example config_rubric.ini")
    logger.info("IMPORTANT: You need to update the canvas_assignment_id values with your actual Canvas assignment IDs")

def show_migration_instructions():
    """Show final migration instructions"""
    logger.info("Migration setup complete!")
    
    print("\n" + "="*60)
    print("MIGRATION TO RUBRIC-BASED SYSTEM")
    print("="*60)
    
    print("\n1. REQUIRED: Update Canvas Assignment IDs")
    print("   - Edit config_rubric.ini")
    print("   - Replace REPLACE_WITH_CANVAS_ID with actual Canvas assignment IDs")
    print("   - Find assignment IDs in Canvas assignment URLs")
    
    print("\n2. REQUIRED: Add Course Documents")
    print("   - Copy your course materials to ../db/text/HIST109 and ../db/text/HIST110")
    print("   - Supported formats: .txt, .md, .pdf, .docx, .lec")
    
    print("\n3. OPTIONAL: Test Canvas API Connection")
    print("   - Run: python -c \"from canvas_rubric_api import get_rubric_for_assignment; print('API test')\"")
    
    print("\n4. RUN THE NEW SYSTEM")
    print("   - streamlit run rubric_grade_ui.py")
    
    print("\n5. FILES YOU CAN REMOVE (backed up in old_system_backup/):")
    print("   - assignment_base.py")
    print("   - assignment_factory.py") 
    print("   - assignment_manager.py")
    print("   - assignment_handlers/ directory")
    print("   - batch_grading_system.py")
    print("   - fixed_batch_grader_v2.py")
    print("   - grade_all_ui.py (replaced by rubric_grade_ui.py)")
    
    print("\n6. KEY DIFFERENCES:")
    print("   - No more module selection - uses Canvas rubrics instead")
    print("   - Evaluates against course documents in ../db/text/")
    print("   - Individual grading only (more reliable with rubrics)")
    print("   - Automatic rubric loading from Canvas")
    
    print("\n" + "="*60)

def main():
    parser = argparse.ArgumentParser(description="Migrate to rubric-based grading system")
    parser.add_argument("--skip-backup", action="store_true", help="Skip backing up old files")
    parser.add_argument("--skip-prereq-check", action="store_true", help="Skip prerequisite check")
    
    args = parser.parse_args()
    
    print("Rubric-Based Grading System Migration")
    print("=====================================")
    
    if not args.skip_prereq_check:
        if not check_prerequisites():
            print("\nPlease resolve the prerequisite issues before continuing.")
            return
    
    if not args.skip_backup:
        backup_old_system()
    
    setup_course_directories()
    create_example_assignment_config()
    show_migration_instructions()

if __name__ == "__main__":
    main()