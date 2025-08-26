"""
Base assignment handler class
All assignment types inherit from this class
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional
import grade_all

class BaseAssignmentHandler(ABC):
    """
    Abstract base class for all assignment type handlers
    """
    
    def __init__(self, assignment_key: str, display_name: str, 
                 default_points: int, course_mapping: Dict[int, int]):
        """
        Initialize the assignment handler
        
        Args:
            assignment_key: Internal key for this assignment type
            display_name: Human-readable name
            default_points: Default scoring scale
            course_mapping: Mapping of course numbers for processing
        """
        self.assignment_key = assignment_key
        self.display_name = display_name
        self.default_points = default_points
        self.course_mapping = course_mapping
    
    @abstractmethod
    def get_lecture_content(self, base_course_number: int, module_number: int) -> str:
        """
        Get lecture content for this assignment type
        
        Args:
            base_course_number: The base course number (109, 110, etc.)
            module_number: The module/topic number
            
        Returns:
            Lecture content string
        """
        pass
    
    @abstractmethod
    def get_questions(self, base_course_number: int, module_number: int) -> str:
        """
        Get assignment questions for this assignment type
        
        Args:
            base_course_number: The base course number (109, 110, etc.)
            module_number: The module/topic number
            
        Returns:
            Questions string
        """
        pass
    
    @abstractmethod
    def get_prompt_string(self, base_course_number: int, module_number: int) -> str:
        """
        Get the complete prompt string for individual grading (legacy method)
        
        Args:
            base_course_number: The base course number (109, 110, etc.)
            module_number: The module/topic number
            
        Returns:
            Complete prompt string
        """
        pass
    
    @abstractmethod
    def requires_module_selection(self) -> bool:
        """
        Does this assignment type require module/topic selection?
        
        Returns:
            True if module selection is required, False otherwise
        """
        pass
    
    @abstractmethod
    def get_available_modules(self, base_course_number: int) -> List[str]:
        """
        Get list of available modules for this assignment type and course
        
        Args:
            base_course_number: The base course number (109, 110, etc.)
            
        Returns:
            List of available module strings
        """
        pass
    
    def get_mapped_course_number(self, base_course_number: int) -> int:
        """
        Get the mapped course number for processing
        
        Args:
            base_course_number: The base course number (109, 110, etc.)
            
        Returns:
            Mapped course number for processing
        """
        return self.course_mapping.get(base_course_number, base_course_number)
    
    def get_grade_scale_info(self) -> Dict[str, str]:
        """
        Get grading scale information for this assignment type
        
        Returns:
            Dictionary with grade scale information
        """
        if self.default_points == 50:
            return {
                "A": "45-50 points (90-100%)",
                "B": "40-44 points (80-89%)",
                "C": "35-39 points (70-79%)",
                "D": "30-34 points (60-69%)",
                "F": "0-29 points (0-59%)"
            }
        elif self.default_points == 225:
            return {
                "A": "203-225 points (90-100%)",
                "B": "180-202 points (80-89%)",
                "C": "158-179 points (70-79%)",
                "D": "135-157 points (60-69%)",
                "F": "0-134 points (0-59%)"
            }
        else:  # 100 or other scales
            percentage_90 = int(self.default_points * 0.9)
            percentage_80 = int(self.default_points * 0.8)
            percentage_70 = int(self.default_points * 0.7)
            percentage_60 = int(self.default_points * 0.6)
            
            return {
                "A": f"{percentage_90}-{self.default_points} points (90-100%)",
                "B": f"{percentage_80}-{percentage_90-1} points (80-89%)",
                "C": f"{percentage_70}-{percentage_80-1} points (70-79%)",
                "D": f"{percentage_60}-{percentage_70-1} points (60-69%)",
                "F": f"0-{percentage_60-1} points (0-59%)"
            }
    
    def validate_configuration(self) -> List[str]:
        """
        Validate this assignment handler's configuration
        
        Returns:
            List of validation messages/warnings
        """
        messages = []
        
        if self.default_points <= 0:
            messages.append(f"Error: Invalid default points for {self.assignment_key}: {self.default_points}")
        
        if not self.course_mapping:
            messages.append(f"Warning: No course mapping defined for {self.assignment_key}")
        
        return messages
    
    def __str__(self) -> str:
        return f"{self.display_name} ({self.assignment_key}): {self.default_points} points"
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.assignment_key}>"