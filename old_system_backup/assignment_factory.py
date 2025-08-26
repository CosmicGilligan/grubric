"""
Assignment Handler Factory
Creates appropriate assignment handlers based on configuration
"""

from typing import Dict, Optional
from assignment_base import BaseAssignmentHandler
from assignment_handlers.module_assignment import ModuleAssignmentHandler
from assignment_handlers.discussion import DiscussionHandler
from assignment_handlers.review import ReviewHandler

class AssignmentHandlerFactory:
    """
    Factory class for creating assignment handlers
    """
    
    # Registry of available handler classes
    _handler_registry = {
        'assignment': ModuleAssignmentHandler,
        'discussion': DiscussionHandler,
        'review': ReviewHandler,
        # Add new assignment types here:
        # 'exam': ExamHandler,
        # 'quiz': QuizHandler,
        # 'project': ProjectHandler,
    }
    
    @classmethod
    def create_handler(cls, assignment_key: str, display_name: str, 
                      internal_type: str, default_points: int, 
                      course_mapping: Dict[int, int]) -> Optional[BaseAssignmentHandler]:
        """
        Create an assignment handler based on the internal type
        
        Args:
            assignment_key: Internal key for the assignment
            display_name: Human-readable name
            internal_type: Type of assignment (assignment, discussion, review, etc.)
            default_points: Default scoring scale
            course_mapping: Course number mapping
            
        Returns:
            Appropriate assignment handler instance or None if type not found
        """
        if internal_type not in cls._handler_registry:
            print(f"Warning: Unknown assignment type '{internal_type}'. Available types: {list(cls._handler_registry.keys())}")
            return None
        
        handler_class = cls._handler_registry[internal_type]
        return handler_class(assignment_key, display_name, default_points, course_mapping)
    
    @classmethod
    def get_available_types(cls) -> list:
        """
        Get list of available assignment types
        
        Returns:
            List of available assignment type strings
        """
        return list(cls._handler_registry.keys())
    
    @classmethod
    def register_handler(cls, internal_type: str, handler_class):
        """
        Register a new assignment handler type
        
        Args:
            internal_type: The internal type string
            handler_class: The handler class (must inherit from BaseAssignmentHandler)
        """
        if not issubclass(handler_class, BaseAssignmentHandler):
            raise ValueError(f"Handler class must inherit from BaseAssignmentHandler")
        
        cls._handler_registry[internal_type] = handler_class
        print(f"Registered new assignment handler: {internal_type} -> {handler_class.__name__}")
    
    @classmethod
    def validate_type(cls, internal_type: str) -> bool:
        """
        Check if an assignment type is supported
        
        Args:
            internal_type: The assignment type to check
            
        Returns:
            True if supported, False otherwise
        """
        return internal_type in cls._handler_registry

# Example of how to add a new assignment type at runtime:
# 
# class ExamHandler(BaseAssignmentHandler):
#     # ... implementation ...
#     pass
# 
# AssignmentHandlerFactory.register_handler('exam', ExamHandler)