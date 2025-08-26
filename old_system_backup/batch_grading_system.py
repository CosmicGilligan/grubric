"""
Enhanced BatchGrader with configurable scoring scales and numerical scores
"""

import anthropic
import re
from typing import List, Dict, Tuple, Optional
import logging

# Set up logging for debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BatchGrader:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self.max_tokens_per_batch = 180000
        self.max_submissions_per_batch = 8
    
    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation (Claude uses ~4 chars per token)"""
        return len(text) // 4
    
    def get_scoring_criteria(self, max_score: int) -> Dict[str, str]:
        """
        Get scoring criteria based on the maximum score
        
        Args:
            max_score: Maximum possible score (50 or 100)
            
        Returns:
            Dictionary with score ranges and descriptions
        """
        if max_score == 50:
            return {
                "excellent": f"{int(max_score * 0.9)}-{max_score} (A)",
                "good": f"{int(max_score * 0.8)}-{int(max_score * 0.89)} (B)", 
                "satisfactory": f"{int(max_score * 0.7)}-{int(max_score * 0.79)} (C)",
                "needs_improvement": f"{int(max_score * 0.6)}-{int(max_score * 0.69)} (D)",
                "unsatisfactory": f"0-{int(max_score * 0.59)} (F)",
                "scale_description": "50-point scale"
            }
        else:  # 100-point scale
            return {
                "excellent": f"{int(max_score * 0.9)}-{max_score} (A)",
                "good": f"{int(max_score * 0.8)}-{int(max_score * 0.89)} (B)",
                "satisfactory": f"{int(max_score * 0.7)}-{int(max_score * 0.79)} (C)",
                "needs_improvement": f"{int(max_score * 0.6)}-{int(max_score * 0.69)} (D)",
                "unsatisfactory": f"0-{int(max_score * 0.59)} (F)",
                "scale_description": "100-point scale"
            }
    
    def create_batch_prompt(self, lecture_content: str, questions: str, 
                          submissions: List[Tuple[str, str]], 
                          max_score: int = 100,
                          assignment_type: str = "assignment") -> str:
        """
        Create a single prompt with configurable scoring scale
        """
        
        scoring_criteria = self.get_scoring_criteria(max_score)
        
        prompt = f"""Grade these {assignment_type} submissions based on the lecture content and questions below.

LECTURE CONTENT:
{lecture_content}

ASSIGNMENT QUESTIONS:
{questions}

SCORING: Use {max_score} points maximum
- A level: {scoring_criteria['excellent']} 
- B level: {scoring_criteria['good']}
- C level: {scoring_criteria['satisfactory']}
- D level: {scoring_criteria['needs_improvement']}
- F level: {scoring_criteria['unsatisfactory']}

FORMAT: For each student, respond with exactly this format:

STUDENT: [Name]
SCORE: [Number]/{max_score}
FEEDBACK: [Your detailed feedback explaining the score]
---

SUBMISSIONS TO GRADE:
"""
        
        for i, (student_name, submission) in enumerate(submissions, 1):
            prompt += f"""
STUDENT: {student_name}
RESPONSE: {submission}
---
"""
        
        prompt += f"""
Grade each submission above. Start with the first student and provide their score and feedback, then move to the next student. Use the exact format shown."""
        
        return prompt
    
    def parse_batch_results_fixed(self, response_text: str, student_names: List[str], max_score: int) -> List[Dict]:
        """
        Fixed parsing that extracts numerical scores and feedback
        """
        logger.info(f"Parsing response of {len(response_text)} characters for {len(student_names)} students (max score: {max_score})")
        
        results = []
        
        # Method 1: Split by "STUDENT:" markers
        student_sections = re.split(r'STUDENT:\s*', response_text, flags=re.IGNORECASE)
        
        # Remove empty first section (before first STUDENT:)
        student_sections = [section.strip() for section in student_sections if section.strip()]
        
        logger.info(f"Found {len(student_sections)} student sections")
        
        for i, section in enumerate(student_sections):
            try:
                # Extract student name (first line after STUDENT:)
                lines = section.split('\n')
                student_line = lines[0].strip()
                
                # Remove any "SCORE:" prefix if it's on the same line
                student_name = re.sub(r'SCORE:.*$', '', student_line).strip()
                
                # Extract numerical score
                score_match = re.search(rf'SCORE:\s*(\d+)(?:/\s*{max_score})?', section, re.IGNORECASE)
                if score_match:
                    numerical_score = int(score_match.group(1))
                    # Validate score is within range
                    numerical_score = min(max(numerical_score, 0), max_score)
                else:
                    # Fallback: look for any number that could be a score
                    number_match = re.search(rf'\b(\d+)/{max_score}\b', section)
                    if number_match:
                        numerical_score = int(number_match.group(1))
                    else:
                        # Default to middle range
                        numerical_score = int(max_score * 0.75)
                
                # Convert to letter grade for compatibility
                if numerical_score >= max_score * 0.9:
                    letter_grade = 'A'
                elif numerical_score >= max_score * 0.8:
                    letter_grade = 'B'
                elif numerical_score >= max_score * 0.7:
                    letter_grade = 'C'
                elif numerical_score >= max_score * 0.6:
                    letter_grade = 'D'
                else:
                    letter_grade = 'F'
                
                # Extract feedback (everything after FEEDBACK: line)
                feedback_match = re.search(r'FEEDBACK:\s*(.*?)(?=---|\Z)', section, re.IGNORECASE | re.DOTALL)
                if feedback_match:
                    feedback = feedback_match.group(1).strip()
                else:
                    # Fallback: use everything after the score line
                    score_line_idx = section.find('SCORE:')
                    if score_line_idx != -1:
                        after_score = section[score_line_idx:].split('\n', 1)
                        feedback = after_score[1] if len(after_score) > 1 else section
                    else:
                        feedback = section
                
                # Clean up feedback
                feedback = re.sub(r'^FEEDBACK:\s*', '', feedback, flags=re.IGNORECASE)
                feedback = re.sub(r'---.*$', '', feedback, flags=re.DOTALL).strip()
                
                # Ensure feedback starts with the numerical score
                if not feedback.startswith(f"{numerical_score}/"):
                    feedback = f"{numerical_score}/{max_score} - {feedback}"
                
                # Validate we have good content
                if len(feedback) < 30:
                    feedback = f"{numerical_score}/{max_score} - Basic assessment. Shows {letter_grade}-level understanding. " + feedback
                
                results.append({
                    'student_name': student_name,
                    'grade': letter_grade,
                    'numerical_score': numerical_score,
                    'max_score': max_score,
                    'feedback': feedback,
                    'full_response': section[:200] + "..."
                })
                
                logger.info(f"Parsed student {i+1}: {student_name} -> {numerical_score}/{max_score} ({letter_grade})")
                
            except Exception as e:
                logger.error(f"Error parsing student section {i}: {e}")
                # Add error result
                fallback_name = student_names[i] if i < len(student_names) else f"Student {i+1}"
                fallback_score = int(max_score * 0.75)  # Default to C-level
                results.append({
                    'student_name': fallback_name,
                    'grade': 'C',
                    'numerical_score': fallback_score,
                    'max_score': max_score,
                    'feedback': f"{fallback_score}/{max_score} - Parsing error occurred. Please review manually.",
                    'full_response': section
                })
        
        # If we didn't get enough results, try alternative parsing
        if len(results) < len(student_names) * 0.8:
            logger.warning(f"Only got {len(results)} results for {len(student_names)} students. Trying alternative parsing...")
            return self._alternative_parsing(response_text, student_names, max_score)
        
        # Pad results if we're missing some
        while len(results) < len(student_names):
            missing_idx = len(results)
            fallback_score = int(max_score * 0.75)
            results.append({
                'student_name': student_names[missing_idx],
                'grade': 'C',
                'numerical_score': fallback_score,
                'max_score': max_score,
                'feedback': f"{fallback_score}/{max_score} - Unable to parse response for this student.",
                'full_response': 'Parsing failed'
            })
        
        return results[:len(student_names)]  # Don't return more than expected
    
    def _alternative_parsing(self, response_text: str, student_names: List[str], max_score: int) -> List[Dict]:
        """
        Alternative parsing method when primary method fails
        """
        logger.info("Using alternative parsing method")
        
        results = []
        
        for student_name in student_names:
            # Look for this student's name in the text
            name_pattern = re.escape(student_name.split()[0])  # Use first name
            
            # Find content near this student's name
            match = re.search(f'{name_pattern}.*?(?=SCORE:|Score:)(.*?)(?=STUDENT:|SUBMISSION|$)', 
                            response_text, re.IGNORECASE | re.DOTALL)
            
            if match:
                student_content = match.group(0)
                
                # Extract numerical score
                score_match = re.search(rf'(\d+)(?:/\s*{max_score})?', student_content)
                numerical_score = int(score_match.group(1)) if score_match else int(max_score * 0.75)
                numerical_score = min(max(numerical_score, 0), max_score)
                
                # Convert to letter grade
                if numerical_score >= max_score * 0.9:
                    letter_grade = 'A'
                elif numerical_score >= max_score * 0.8:
                    letter_grade = 'B'
                elif numerical_score >= max_score * 0.7:
                    letter_grade = 'C'
                elif numerical_score >= max_score * 0.6:
                    letter_grade = 'D'
                else:
                    letter_grade = 'F'
                
                # Use the matched content as feedback
                feedback = student_content.strip()
                if not feedback.startswith(f"{numerical_score}/"):
                    feedback = f"{numerical_score}/{max_score} - {feedback}"
                
            else:
                # No match found, create minimal response
                numerical_score = int(max_score * 0.75)
                letter_grade = 'C'
                feedback = f"{numerical_score}/{max_score} - Unable to locate detailed feedback for {student_name} in batch response."
            
            results.append({
                'student_name': student_name,
                'grade': letter_grade,
                'numerical_score': numerical_score,
                'max_score': max_score,
                'feedback': feedback,
                'full_response': f"Alternative parsing used for {student_name}"
            })
        
        return results
    
    def grade_batch(self, lecture_content: str, questions: str, 
                   submissions: List[Tuple[str, str]], 
                   max_score: int = 100,
                   assignment_type: str = "assignment") -> List[Dict]:
        """
        Grade a single batch of submissions with configurable scoring
        """
        logger.info(f"Grading batch of {len(submissions)} submissions (max score: {max_score})")
        
        prompt = self.create_batch_prompt(lecture_content, questions, submissions, max_score, assignment_type)
        
        try:
            message = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=8000,
                system="You are grading student work. Grade each student immediately using the exact format shown. Do not provide meta-commentary about grading - just grade each student one by one.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            response_text = message.content[0].text
            logger.info(f"Received response of {len(response_text)} characters")
            
            student_names = [name for name, _ in submissions]
            results = self.parse_batch_results_fixed(response_text, student_names, max_score)
            
            logger.info(f"Successfully parsed {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"Error in batch grading: {e}")
            # Return error results for all submissions in batch
            return [
                {
                    'student_name': name,
                    'grade': 'ERROR',
                    'numerical_score': 0,
                    'max_score': max_score,
                    'feedback': f'0/{max_score} - Grading error: {str(e)}',
                    'full_response': f'Error: {str(e)}'
                }
                for name, _ in submissions
            ]
    
    def grade_all_submissions(self, lecture_content: str, questions: str, 
                            submissions: List[Tuple[str, str]], 
                            max_score: int = 100,
                            assignment_type: str = "assignment",
                            progress_callback=None) -> List[Dict]:
        """
        Grade all submissions using configurable scoring scale
        """
        batches = self.create_optimal_batches(submissions, lecture_content, questions)
        all_results = []
        
        if progress_callback:
            progress_callback(f"Processing {len(submissions)} submissions in {len(batches)} batches (max score: {max_score})...")
        
        for i, batch in enumerate(batches):
            if progress_callback:
                progress_callback(f"Processing batch {i+1}/{len(batches)} ({len(batch)} submissions)...")
            
            batch_results = self.grade_batch(lecture_content, questions, batch, max_score, assignment_type)
            all_results.extend(batch_results)
            
            if progress_callback:
                progress_callback(f"Completed batch {i+1}/{len(batches)}")
        
        return all_results
    
    def create_optimal_batches(self, submissions: List[Tuple[str, str]], 
                             lecture_content: str, questions: str) -> List[List[Tuple[str, str]]]:
        """
        Create smaller, more manageable batches
        """
        batches = []
        current_batch = []
        
        batch_size = min(self.max_submissions_per_batch, 8)
        
        for i, submission in enumerate(submissions):
            current_batch.append(submission)
            
            if len(current_batch) >= batch_size or i == len(submissions) - 1:
                batches.append(current_batch)
                current_batch = []
        
        logger.info(f"Created {len(batches)} batches from {len(submissions)} submissions")
        return batches