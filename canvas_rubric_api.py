"""
Canvas API integration for rubric retrieval and assignment management
"""

import requests
import json
from typing import Dict, List, Optional, Tuple
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CanvasRubricAPI:
    def __init__(self, canvas_url: str, api_token: str):
        """
        Initialize Canvas API client
        
        Args:
            canvas_url: Base Canvas URL (e.g., 'https://yourschool.instructure.com')
            api_token: Canvas API token
        """
        self.canvas_url = canvas_url.rstrip('/')
        self.api_token = api_token
        self.headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }
    
    def get_assignment_with_rubric(self, course_id: str, assignment_id: str) -> Dict:
        """
        Get assignment data including rubric information
        
        Args:
            course_id: Canvas course ID
            assignment_id: Canvas assignment ID
            
        Returns:
            Dictionary containing assignment and rubric data
        """
        url = f"{self.canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"
        params = {
            'include[]': ['rubric']
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            assignment_data = response.json()
            logger.info(f"Retrieved assignment {assignment_id} with rubric")
            return assignment_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching assignment {assignment_id}: {e}")
            return {}
    
    def get_discussion_assignment_id(self, course_id: str, discussion_topic_id: str) -> Optional[str]:
        """
        Find the assignment ID associated with a discussion topic
        
        Args:
            course_id: Canvas course ID
            discussion_topic_id: Canvas discussion topic ID
            
        Returns:
            Associated assignment ID, or None if not found
        """
        url = f"{self.canvas_url}/api/v1/courses/{course_id}/discussion_topics/{discussion_topic_id}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            discussion_data = response.json()
            assignment_id = discussion_data.get('assignment_id')
            
            if assignment_id:
                logger.info(f"Found assignment ID {assignment_id} for discussion topic {discussion_topic_id}")
            else:
                logger.warning(f"No assignment ID found for discussion topic {discussion_topic_id}")
            
            return str(assignment_id) if assignment_id else None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching discussion topic {discussion_topic_id}: {e}")
            return None
    
    def get_discussion_with_rubric(self, course_id: str, discussion_topic_id: str) -> Dict:
        """
        Get discussion data including rubric information
        
        Args:
            course_id: Canvas course ID
            discussion_topic_id: Canvas discussion topic ID
            
        Returns:
            Dictionary containing assignment and rubric data
        """
        # First, find the associated assignment ID
        assignment_id = self.get_discussion_assignment_id(course_id, discussion_topic_id)
        
        if not assignment_id:
            logger.error(f"Could not find assignment ID for discussion topic {discussion_topic_id}")
            return {}
        
        # Now get the assignment with its rubric
        return self.get_assignment_with_rubric(course_id, assignment_id)
    
    def get_course_assignments(self, course_id: str) -> List[Dict]:
        """
        Get all assignments for a course
        
        Args:
            course_id: Canvas course ID
            
        Returns:
            List of assignment dictionaries
        """
        url = f"{self.canvas_url}/api/v1/courses/{course_id}/assignments"
        params = {
            'include[]': ['rubric'],
            'per_page': 100
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            assignments = response.json()
            logger.info(f"Retrieved {len(assignments)} assignments for course {course_id}")
            return assignments
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching assignments for course {course_id}: {e}")
            return []
    
    def parse_rubric_criteria(self, rubric_data: Dict) -> List[Dict]:
        """
        Parse rubric data into structured criteria
        
        Args:
            rubric_data: Raw rubric data from Canvas API
            
        Returns:
            List of rubric criteria with ratings
        """
        criteria = []
        
        if not rubric_data:
            return criteria
        
        for criterion in rubric_data:
            criterion_data = {
                'id': criterion.get('id'),
                'description': criterion.get('description', ''),
                'long_description': criterion.get('long_description', ''),
                'points': float(criterion.get('points', 0)),
                'ratings': []
            }
            
            # Parse rating levels
            for rating in criterion.get('ratings', []):
                rating_data = {
                    'id': rating.get('id'),
                    'description': rating.get('description', ''),
                    'long_description': rating.get('long_description', ''),
                    'points': float(rating.get('points', 0))
                }
                criterion_data['ratings'].append(rating_data)
            
            # Sort ratings by points (highest to lowest)
            criterion_data['ratings'].sort(key=lambda x: x['points'], reverse=True)
            criteria.append(criterion_data)
        
        return criteria
    
    def get_rubric_total_points(self, criteria: List[Dict]) -> float:
        """
        Calculate total possible points from rubric criteria
        
        Args:
            criteria: List of parsed rubric criteria
            
        Returns:
            Total possible points
        """
        return sum(criterion['points'] for criterion in criteria)
    
    def format_rubric_for_grading(self, criteria: List[Dict]) -> str:
        """
        Format rubric criteria for use in grading prompts
        
        Args:
            criteria: List of parsed rubric criteria
            
        Returns:
            Formatted string describing the rubric
        """
        rubric_text = "GRADING RUBRIC:\n\n"
        
        for i, criterion in enumerate(criteria, 1):
            rubric_text += f"CRITERION {i}: {criterion['description']} ({criterion['points']} points)\n"
            
            if criterion['long_description']:
                rubric_text += f"Details: {criterion['long_description']}\n"
            
            rubric_text += "Rating Scale:\n"
            for rating in criterion['ratings']:
                rubric_text += f"  - {rating['description']} ({rating['points']} pts)"
                if rating['long_description']:
                    rubric_text += f": {rating['long_description']}"
                rubric_text += "\n"
            
            rubric_text += "\n"
        
        return rubric_text
    
    def get_assignment_submissions(self, course_id: str, assignment_id: str) -> List[Dict]:
        """
        Get all submissions for an assignment
        
        Args:
            course_id: Canvas course ID
            assignment_id: Canvas assignment ID
            
        Returns:
            List of submission data
        """
        url = f"{self.canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
        params = {
            'include[]': ['user'],
            'per_page': 100
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            submissions = response.json()
            logger.info(f"Retrieved {len(submissions)} submissions for assignment {assignment_id}")
            return submissions
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching submissions for assignment {assignment_id}: {e}")
            return []

def load_canvas_credentials(secrets_file: str = '/home/drkeithcox/canvas-secrets.key') -> Tuple[str, str]:
    """
    Load Canvas URL and API token from secrets file
    
    Args:
        secrets_file: Path to secrets file
        
    Returns:
        Tuple of (canvas_url, api_token)
    """
    try:
        with open(secrets_file, 'r') as f:
            lines = [line.strip() for line in f]
        
        if len(lines) >= 2:
            canvas_url = lines[0]
            api_token = lines[1]
            return canvas_url, api_token
        else:
            raise ValueError("Secrets file must contain at least Canvas URL and API token")
            
    except FileNotFoundError:
        logger.error(f"Canvas secrets file not found: {secrets_file}")
        raise
    except Exception as e:
        logger.error(f"Error reading Canvas secrets: {e}")
        raise

# Convenience function for quick rubric retrieval
def get_rubric_for_assignment(course_id: str, assignment_id: str, is_discussion: bool = False) -> Dict:
    """
    Quick function to get rubric data for a specific assignment or discussion
    
    Args:
        course_id: Canvas course ID
        assignment_id: Canvas assignment ID or discussion topic ID
        is_discussion: True if this is a discussion topic ID
        
    Returns:
        Dictionary with assignment and parsed rubric data
    """
    try:
        canvas_url, api_token = load_canvas_credentials()
        canvas_api = CanvasRubricAPI(canvas_url, api_token)
        
        # Get assignment data - different method for discussions
        if is_discussion:
            assignment_data = canvas_api.get_discussion_with_rubric(course_id, assignment_id)
        else:
            assignment_data = canvas_api.get_assignment_with_rubric(course_id, assignment_id)
        
        if not assignment_data:
            return {}
        
        rubric_raw = assignment_data.get('rubric', [])
        rubric_criteria = canvas_api.parse_rubric_criteria(rubric_raw)
        
        # Use points_possible from Canvas assignment, not sum of rubric criteria
        points_possible = assignment_data.get('points_possible')
        if points_possible is not None:
            total_points = float(points_possible)
            logger.info(f"Using Canvas assignment points_possible: {total_points}")
        else:
            # Fallback to calculating from rubric if points_possible not available
            total_points = canvas_api.get_rubric_total_points(rubric_criteria)
            logger.warning(f"points_possible not found, calculated from rubric criteria: {total_points}")
        
        return {
            'assignment': assignment_data,
            'rubric_criteria': rubric_criteria,
            'total_points': total_points,
            'formatted_rubric': canvas_api.format_rubric_for_grading(rubric_criteria)
        }
        
    except Exception as e:
        logger.error(f"Error retrieving rubric: {e}")
        return {}