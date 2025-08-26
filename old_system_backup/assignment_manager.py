"""
Assignment Management System
High-level interface for working with different assignment types
"""

from typing import Dict, List, Optional, Tuple
from config_manager import ConfigManager
from assignment_factory import AssignmentHandlerFactory
from assignment_base import BaseAssignmentHandler

class AssignmentManager:
    """
    High-level manager for assignment handlers
    Provides a clean interface between the UI and assignment-specific logic
    """
    
    def __init__(self, config_manager: ConfigManager):
        """
        Initialize the assignment manager
        
        Args:
            config_manager: Configuration manager instance
        """
        self.config = config_manager
        self.handlers = {}
        self._load_handlers()
    
    def _load_handlers(self):
        """Load all assignment handlers from configuration"""
        assignment_types = self.config.get_assignment_types()
        
        for display_name, key, internal_type, default_points, course_mapping in assignment_types:
            handler = AssignmentHandlerFactory.create_handler(
                key, display_name, internal_type, default_points, course_mapping
            )
            
            if handler:
                self.handlers[key] = handler
            else:
                print(f"Warning: Could not create handler for assignment type: {key} ({internal_type})")
    
    def get_handler(self, assignment_key: str) -> Optional[BaseAssignmentHandler]:
        """
        Get a specific assignment handler
        
        Args:
            assignment_key: The assignment key
            
        Returns:
            Assignment handler or None if not found
        """
        return self.handlers.get(assignment_key)
    
    def get_all_handlers(self) -> Dict[str, BaseAssignmentHandler]:
        """
        Get all available assignment handlers
        
        Returns:
            Dictionary of assignment_key -> handler
        """
        return self.handlers.copy()
    
    def get_assignment_options(self) -> List[Tuple[str, str]]:
        """
        Get assignment options for UI display
        
        Returns:
            List of (display_name, assignment_key) tuples
        """
        return [(handler.display_name, key) for key, handler in self.handlers.items()]
    
    def requires_module_selection(self, assignment_key: str) -> bool:
        """
        Check if an assignment type requires module selection
        
        Args:
            assignment_key: The assignment key
            
        Returns:
            True if module selection is required
        """
        handler = self.get_handler(assignment_key)
        return handler.requires_module_selection() if handler else False
    
    def get_available_modules(self, assignment_key: str, base_course_number: int) -> List[str]:
        """
        Get available modules for an assignment and course
        
        Args:
            assignment_key: The assignment key
            base_course_number: The base course number
            
        Returns:
            List of available module strings
        """
        handler = self.get_handler(assignment_key)
        return handler.get_available_modules(base_course_number) if handler else []
    
    def get_grading_data(self, assignment_key: str, base_course_number: int, 
                        module_number: Optional[int] = None) -> Dict:
        """
        Get all data needed for grading an assignment
        
        Args:
            assignment_key: The assignment key
            base_course_number: The base course number
            module_number: The module number (if required)
            
        Returns:
            Dictionary with lecture_content, questions, max_score, etc.
        """
        handler = self.get_handler(assignment_key)
        if not handler:
            raise ValueError(f"No handler found for assignment type: {assignment_key}")
        
        result = {
            'assignment_key': assignment_key,
            'display_name': handler.display_name,
            'max_score': handler.default_points,
            'requires_module': handler.requires_module_selection(),
            'base_course_number': base_course_number,
            'mapped_course_number': handler.get_mapped_course_number(base_course_number)
        }
        
        if handler.requires_module_selection():
            if module_number is None:
                raise ValueError(f"Module number required for assignment type: {assignment_key}")
            
            result.update({
                'module_number': module_number,
                'lecture_content': handler.get_lecture_content(base_course_number, module_number),
                'questions': handler.get_questions(base_course_number, module_number),
                'prompt_string': handler.get_prompt_string(base_course_number, module_number)
            })
        else:
            result.update({
                'lecture_content': handler.get_lecture_content(base_course_number, 0),
                'questions': handler.get_questions(base_course_number, 0),
                'prompt_string': handler.get_prompt_string(base_course_number, 0)
            })
        
        # Add assignment-specific context if available
        if hasattr(handler, 'get_assignment_context'):
            result['assignment_context'] = handler.get_assignment_context(result['questions'])
        
        if hasattr(handler, 'get_evaluation_instruction'):
            result['evaluation_instruction'] = handler.get_evaluation_instruction(result['questions'])
        
        return result
    
    def get_grade_scale_info(self, assignment_key: str) -> Dict[str, str]:
        """
        Get grade scale information for an assignment type
        
        Args:
            assignment_key: The assignment key
            
        Returns:
            Dictionary with grade scale information
        """
        handler = self.get_handler(assignment_key)
        return handler.get_grade_scale_info() if handler else {}
    
    def validate_all_handlers(self) -> List[str]:
        """
        Validate all assignment handlers
        
        Returns:
            List of validation messages
        """
        messages = []
        
        for key, handler in self.handlers.items():
            handler_messages = handler.validate_configuration()
            messages.extend([f"[{key}] {msg}" for msg in handler_messages])
        
        return messages
    
    def reload_handlers(self):
        """Reload all handlers from updated configuration"""
        self.handlers.clear()
        self._load_handlers()
    
    def get_handler_info(self, assignment_key: str) -> Dict:
        """
        Get detailed information about a specific handler
        
        Args:
            assignment_key: The assignment key
            
        Returns:
            Dictionary with handler information
        """
        handler = self.get_handler(assignment_key)
        if not handler:
            return {}
        
        info = {
            'assignment_key': assignment_key,
            'display_name': handler.display_name,
            'default_points': handler.default_points,
            'course_mapping': handler.course_mapping,
            'requires_module': handler.requires_module_selection(),
            'grade_scale': handler.get_grade_scale_info()
        }
        
        # Add extra info if available
        if hasattr(handler, 'get_grading_criteria'):
            info['grading_criteria'] = handler.get_grading_criteria()
        
        if hasattr(handler, 'get_special_instructions'):
            info['special_instructions'] = handler.get_special_instructions()
        
        return info
    
    def __str__(self) -> str:
        return f"AssignmentManager with {len(self.handlers)} handlers: {list(self.handlers.keys())}"