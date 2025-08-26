"""
Configuration manager for rubric-based grading system
"""

import configparser
import os
from typing import Dict, List, Tuple, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RubricConfigManager:
    def __init__(self, config_file_path: str = "config_rubric.ini"):
        """
        Initialize the rubric configuration manager
        
        Args:
            config_file_path: Path to the configuration INI file
        """
        self.config_file_path = config_file_path
        self.config = configparser.ConfigParser()
        self.load_config()
    
    def load_config(self) -> None:
        """Load configuration from INI file"""
        if not os.path.exists(self.config_file_path):
            self.create_default_config()
        
        self.config.read(self.config_file_path)
    
    def create_default_config(self) -> None:
        """Create a default configuration file if none exists"""
        default_config = """# Configuration file for Rubric-Based Grading System
# Update this file with Canvas assignment IDs and course document paths

[COURSES]
# Format: course_name = Display Name, CRN
hist109_section1 = HIST109 Section 1, 2480616
hist109_section2 = HIST109 Section 2, 2480619
hist110 = HIST110, 2486450

[RUBRIC_ASSIGNMENTS]
# Format: assignment_key = Display Name, canvas_assignment_id, course_id, documents_path
module_assignment_109 = Module Assignment HIST109, 12345, 2480616, ../db/text/HIST109
discussion_109 = Discussion Post HIST109, 12346, 2480616, ../db/text/HIST109
final_essay_109 = Final Essay HIST109, 12347, 2480616, ../db/text/HIST109

[COURSE_DOCUMENT_PATHS]
# Course-specific document paths
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
        
        with open(self.config_file_path, 'w') as f:
            f.write(default_config)
        
        logger.info(f"Created default configuration file: {self.config_file_path}")
    
    def get_courses(self) -> List[Tuple[str, str]]:
        """
        Get list of available courses
        
        Returns:
            List of tuples: (display_name, crn)
        """
        courses = []
        if 'COURSES' in self.config:
            for key, value in self.config['COURSES'].items():
                parts = [part.strip() for part in value.split(',')]
                if len(parts) == 2:
                    display_name, crn = parts
                    courses.append((display_name, crn))
                else:
                    logger.warning(f"Invalid course configuration for {key}: {value}")
        
        return courses
    
    def get_rubric_assignments(self) -> List[Tuple[str, str, str, str, str]]:
        """
        Get list of available rubric assignments
        
        Returns:
            List of tuples: (display_name, assignment_key, canvas_assignment_id, course_id, documents_path)
        """
        assignments = []
        if 'RUBRIC_ASSIGNMENTS' in self.config:
            for key, value in self.config['RUBRIC_ASSIGNMENTS'].items():
                parts = [part.strip() for part in value.split(',')]
                if len(parts) == 4:
                    display_name, canvas_assignment_id, course_id, documents_path = parts
                    assignments.append((display_name, key, canvas_assignment_id, course_id, documents_path))
                else:
                    logger.warning(f"Invalid assignment configuration for {key}: {value}")
        
        return assignments
    
    def get_assignment_by_key(self, assignment_key: str) -> Optional[Tuple[str, str, str, str]]:
        """
        Get assignment configuration by key
        
        Args:
            assignment_key: The assignment key to look up
            
        Returns:
            Tuple of (display_name, canvas_assignment_id, course_id, documents_path) or None
        """
        assignments = self.get_rubric_assignments()
        for display_name, key, canvas_assignment_id, course_id, documents_path in assignments:
            if key == assignment_key:
                return (display_name, canvas_assignment_id, course_id, documents_path)
        return None
    
    def get_course_document_path(self, course_key: str) -> str:
        """
        Get document path for a course
        
        Args:
            course_key: Course identifier (e.g., 'HIST109')
            
        Returns:
            Path to course documents
        """
        if 'COURSE_DOCUMENT_PATHS' in self.config and course_key in self.config['COURSE_DOCUMENT_PATHS']:
            return self.config['COURSE_DOCUMENT_PATHS'][course_key]
        
        # Fallback to default pattern
        base_path = self.get_path('course_documents_base', '../db/text/')
        return os.path.join(base_path, course_key)
    
    def get_setting(self, setting_name: str, default_value: str = "") -> str:
        """
        Get a setting value
        
        Args:
            setting_name: Name of the setting
            default_value: Default value if setting not found
            
        Returns:
            Setting value
        """
        if 'SETTINGS' in self.config and setting_name in self.config['SETTINGS']:
            return self.config['SETTINGS'][setting_name]
        return default_value
    
    def get_api_setting(self, setting_name: str, default_value: str = "") -> str:
        """Get an API setting value"""
        if 'API_SETTINGS' in self.config and setting_name in self.config['API_SETTINGS']:
            return self.config['API_SETTINGS'][setting_name]
        return default_value
    
    def get_path(self, path_name: str, default_value: str = "") -> str:
        """
        Get a path value
        
        Args:
            path_name: Name of the path
            default_value: Default value if path not found
            
        Returns:
            Path value
        """
        if 'PATHS' in self.config and path_name in self.config['PATHS']:
            return self.config['PATHS'][path_name]
        return default_value
    
    def get_grading_scale(self) -> Dict[str, float]:
        """Get grading scale percentages"""
        scale = {
            'A': 90.0,
            'B': 80.0,
            'C': 70.0,
            'D': 60.0
        }
        
        if 'GRADING_SCALES' in self.config:
            scale['A'] = float(self.config['GRADING_SCALES'].get('grade_a_min', 90.0))
            scale['B'] = float(self.config['GRADING_SCALES'].get('grade_b_min', 80.0))
            scale['C'] = float(self.config['GRADING_SCALES'].get('grade_c_min', 70.0))
            scale['D'] = float(self.config['GRADING_SCALES'].get('grade_d_min', 60.0))
        
        return scale
    
    def get_embedding_settings(self) -> Dict[str, any]:
        """Get embedding-related settings"""
        settings = {
            'embedding_model': 'all-MiniLM-L6-v2',
            'batch_size': 32,
            'force_refresh_embeddings': False
        }
        
        if 'EMBEDDING_SETTINGS' in self.config:
            settings['embedding_model'] = self.config['EMBEDDING_SETTINGS'].get('embedding_model', 'all-MiniLM-L6-v2')
            settings['batch_size'] = int(self.config['EMBEDDING_SETTINGS'].get('batch_size', 32))
            settings['force_refresh_embeddings'] = self.config['EMBEDDING_SETTINGS'].getboolean('force_refresh_embeddings', False)
        
        return settings
    
    def get_course_number_from_name(self, course_display_name: str) -> Optional[str]:
        """
        Extract course identifier from display name
        
        Args:
            course_display_name: Display name like "HIST109 Section 1"
            
        Returns:
            Course identifier (HIST109, HIST110, etc.) or None
        """
        import re
        
        # Look for pattern like "HIST109" or "HIST110"
        match = re.search(r'HIST(\d+)', course_display_name.upper())
        if match:
            return f"HIST{match.group(1)}"
        
        return None
    
    def get_assignments_for_course(self, course_id: str) -> List[Tuple[str, str]]:
        """
        Get assignments available for a specific course
        
        Args:
            course_id: Canvas course ID (CRN)
            
        Returns:
            List of (display_name, assignment_key) tuples
        """
        assignments = []
        rubric_assignments = self.get_rubric_assignments()
        
        for display_name, assignment_key, canvas_assignment_id, assignment_course_id, documents_path in rubric_assignments:
            if assignment_course_id == course_id:
                assignments.append((display_name, assignment_key))
        
        return assignments
    
    def reload_config(self) -> None:
        """Reload configuration from file"""
        self.load_config()
        logger.info("Configuration reloaded")
    
    def validate_config(self) -> List[str]:
        """
        Validate the configuration file and return any warnings/errors
        
        Returns:
            List of validation messages
        """
        messages = []
        
        # Check required sections
        required_sections = ['COURSES', 'RUBRIC_ASSIGNMENTS', 'SETTINGS', 'PATHS']
        for section in required_sections:
            if section not in self.config:
                messages.append(f"Warning: Missing section [{section}]")
        
        # Validate courses
        courses = self.get_courses()
        if not courses:
            messages.append("Error: No courses configured")
        
        # Validate rubric assignments
        assignments = self.get_rubric_assignments()
        if not assignments:
            messages.append("Error: No rubric assignments configured")
        
        # Check document paths exist
        for display_name, key, canvas_id, course_id, doc_path in assignments:
            if not os.path.exists(doc_path):
                messages.append(f"Warning: Document path does not exist for {key}: {doc_path}")
        
        # Check API credentials file
        secrets_file = self.get_api_setting('canvas_secrets_file', '/home/drkeithcox/canvas-secrets.key')
        if not os.path.exists(secrets_file):
            messages.append(f"Error: Canvas secrets file not found: {secrets_file}")
        
        anthropic_key_file = self.get_api_setting('anthropic_key_file', '/home/drkeithcox/anthropic.key')
        if not os.path.exists(anthropic_key_file):
            messages.append(f"Error: Anthropic key file not found: {anthropic_key_file}")
        
        return messages
    
    def add_assignment(self, assignment_key: str, display_name: str, 
                      canvas_assignment_id: str, course_id: str, documents_path: str):
        """Add a new assignment to the configuration"""
        if 'RUBRIC_ASSIGNMENTS' not in self.config:
            self.config.add_section('RUBRIC_ASSIGNMENTS')
        
        value = f"{display_name}, {canvas_assignment_id}, {course_id}, {documents_path}"
        self.config['RUBRIC_ASSIGNMENTS'][assignment_key] = value
        self.save_config()
        logger.info(f"Added assignment: {assignment_key}")
    
    def add_course(self, course_key: str, display_name: str, crn: str):
        """Add a new course to the configuration"""
        if 'COURSES' not in self.config:
            self.config.add_section('COURSES')
        
        self.config['COURSES'][course_key] = f"{display_name}, {crn}"
        self.save_config()
        logger.info(f"Added course: {course_key}")
    
    def save_config(self) -> None:
        """Save current configuration to file"""
        with open(self.config_file_path, 'w') as f:
            self.config.write(f)
        logger.info("Configuration saved")
    
    def __str__(self) -> str:
        courses = len(self.get_courses())
        assignments = len(self.get_rubric_assignments())
        return f"RubricConfigManager: {courses} courses, {assignments} assignments"

# Global configuration manager instance
rubric_config_manager = RubricConfigManager()

def get_rubric_config() -> RubricConfigManager:
    """Get the global rubric configuration manager instance"""
    return rubric_config_manager