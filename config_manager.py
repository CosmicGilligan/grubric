"""
Configuration manager for the grading system
Handles loading and parsing of config.ini file
"""

import configparser
import os
from typing import Dict, List, Tuple, Optional

class ConfigManager:
    def __init__(self, config_file_path: str = "config.ini"):
        """
        Initialize the configuration manager
        
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
        default_config = """# Configuration file for Professor Cosmic's Magic Grading System
# Update this file each semester with new CRNs and assignment types

[COURSES]
# Format: course_name = Display Name, CRN
hist109_section1 = HIST109 Section 1, 2480616
hist109_section2 = HIST109 Section 2, 2480619
hist110 = HIST110, 2486450

[ASSIGNMENT_TYPES]
# Format: assignment_key = Display Name, internal_type, default_points, course_number_mapping
# The default_points value is the scoring scale used for each assignment type
module_assignment = Module Assignment, assignment, 100, 109:109;110:110
discussion = Discussion, discussion, 50, 109:111;110:112
review = Review, review, 225, 109:109;110:110

[SETTINGS]
default_processing_method = Individual (Reliable)
default_batch_size = 4
default_debug_mode = False
default_model = claude-3-5-sonnet-20241022

[PATHS]
submissions_directory = ./submissions/
transcripts_directory = ../db/Transcripts/
secrets_file = /home/drkeithcox/canvas-secrets.key
"""
        
        with open(self.config_file_path, 'w') as f:
            f.write(default_config)
        
        print(f"Created default configuration file: {self.config_file_path}")
    
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
                    print(f"Warning: Invalid course configuration for {key}: {value}")
        
        return courses
    
    def get_assignment_types(self) -> List[Tuple[str, str, str, int, Dict[int, int]]]:
        """
        Get list of available assignment types
        
        Returns:
            List of tuples: (display_name, assignment_key, internal_type, default_points, course_mapping)
        """
        assignment_types = []
        if 'ASSIGNMENT_TYPES' in self.config:
            for key, value in self.config['ASSIGNMENT_TYPES'].items():
                parts = [part.strip() for part in value.split(',')]
                if len(parts) >= 4:
                    display_name = parts[0]
                    internal_type = parts[1]
                    default_points = int(parts[2])
                    
                    # Parse course number mapping (e.g., "109:109;110:112")
                    course_mapping = {}
                    if len(parts) > 3:
                        mapping_str = parts[3]
                        for mapping in mapping_str.split(';'):
                            if ':' in mapping:
                                source, target = mapping.split(':')
                                course_mapping[int(source.strip())] = int(target.strip())
                    
                    assignment_types.append((display_name, key, internal_type, default_points, course_mapping))
                else:
                    print(f"Warning: Invalid assignment type configuration for {key}: {value}")
        
        return assignment_types
    
    def get_scoring_scale(self, assignment_key: str) -> int:
        """
        Get the default scoring scale for an assignment type
        
        Args:
            assignment_key: The assignment type key
            
        Returns:
            Default point value for this assignment type
        """
        assignment_types = self.get_assignment_types()
        for display_name, key, internal_type, default_points, course_mapping in assignment_types:
            if key == assignment_key:
                return default_points
        
        return 100  # Default fallback
    
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
    
    def get_course_number_from_name(self, course_display_name: str) -> Optional[int]:
        """
        Extract course number from display name
        
        Args:
            course_display_name: Display name like "HIST109 Section 1"
            
        Returns:
            Course number (109, 110, etc.) or None
        """
        if 'HIST109' in course_display_name:
            return 109
        elif 'HIST110' in course_display_name:
            return 110
        
        # Try to extract number from the name
        import re
        match = re.search(r'HIST(\d+)', course_display_name)
        if match:
            return int(match.group(1))
        
        return None
    
    def get_mapped_course_number(self, base_course_number: int, assignment_key: str) -> int:
        """
        Get the mapped course number for an assignment type
        
        Args:
            base_course_number: The base course number (109, 110, etc.)
            assignment_key: The assignment type key
            
        Returns:
            Mapped course number for the assignment processing
        """
        assignment_types = self.get_assignment_types()
        for display_name, key, internal_type, default_points, course_mapping in assignment_types:
            if key == assignment_key:
                return course_mapping.get(base_course_number, base_course_number)
        
        return base_course_number
    
    def reload_config(self) -> None:
        """Reload configuration from file"""
        self.load_config()
    
    def update_course(self, course_key: str, display_name: str, crn: str) -> None:
        """
        Update or add a course in the configuration
        
        Args:
            course_key: Internal key for the course
            display_name: Display name for the course
            crn: Course CRN
        """
        if 'COURSES' not in self.config:
            self.config.add_section('COURSES')
        
        self.config['COURSES'][course_key] = f"{display_name}, {crn}"
        self.save_config()
    
    def update_assignment_type(self, assignment_key: str, display_name: str, 
                             internal_type: str, default_points: int, 
                             course_mapping: Dict[int, int]) -> None:
        """
        Update or add an assignment type in the configuration
        
        Args:
            assignment_key: Internal key for the assignment type
            display_name: Display name for the assignment type
            internal_type: Internal processing type
            default_points: Default point value
            course_mapping: Dictionary mapping course numbers
        """
        if 'ASSIGNMENT_TYPES' not in self.config:
            self.config.add_section('ASSIGNMENT_TYPES')
        
        # Format course mapping
        mapping_str = ';'.join([f"{k}:{v}" for k, v in course_mapping.items()])
        
        self.config['ASSIGNMENT_TYPES'][assignment_key] = f"{display_name}, {internal_type}, {default_points}, {mapping_str}"
        self.save_config()
    
    def save_config(self) -> None:
        """Save current configuration to file"""
        with open(self.config_file_path, 'w') as f:
            self.config.write(f)
    
    def validate_config(self) -> List[str]:
        """
        Validate the configuration file and return any warnings/errors
        
        Returns:
            List of validation messages
        """
        messages = []
        
        # Check required sections
        required_sections = ['COURSES', 'ASSIGNMENT_TYPES', 'SETTINGS', 'PATHS']
        for section in required_sections:
            if section not in self.config:
                messages.append(f"Warning: Missing section [{section}]")
        
        # Validate courses
        courses = self.get_courses()
        if not courses:
            messages.append("Error: No courses configured")
        
        # Validate assignment types
        assignment_types = self.get_assignment_types()
        if not assignment_types:
            messages.append("Error: No assignment types configured")
        
        # Check if scoring scales are defined for all assignment types
        for display_name, key, internal_type, default_points, course_mapping in assignment_types:
            if default_points <= 0:
                messages.append(f"Warning: Invalid default points for assignment type '{key}': {default_points}")
        
        return messages

# Global configuration manager instance
config_manager = ConfigManager()

def get_config() -> ConfigManager:
    """Get the global configuration manager instance"""
    return config_manager