import re
import anthropic
from typing import List, Tuple, Optional, Callable
import streamlit as st

class SimpleIndividualGrader:
    def __init__(self, client):
        self.client = client
        
    def clean_feedback(self, feedback: str) -> str:
        """Remove greetings and closings from feedback - now uses comprehensive cleaner"""
        # Import the function from grade_all
        from grade_all import clean_feedback_comprehensive
        return clean_feedback_comprehensive(feedback)
        
    def detect_question_count(self, questions: str) -> int:
        """Detect number of questions in assignment"""
        if not questions:
            return 1
            
        # Count numbered questions (1., 2., 3., etc.)
        numbered_questions = len([line for line in questions.split('\n') 
                                if any(line.strip().startswith(f"{i}.") for i in range(1, 21))])
        
        if numbered_questions > 1:
            return numbered_questions
            
        # Fallback: count question marks and colons
        question_marks = questions.count('?')
        colons = questions.count(':')
        
        return max(1, question_marks + colons)
    
    def extract_grade_and_feedback(self, response: str, max_score: int) -> Tuple[int, str, str]:
        """Extract numerical score, letter grade, and feedback from Claude's response"""
        try:
            # Look for score patterns
            score_patterns = [
                rf'(\d+)/{max_score}',
                rf'SCORE:\s*(\d+)/{max_score}',
                rf'Score:\s*(\d+)/{max_score}',
                rf'Grade:\s*(\d+)/{max_score}',
                rf'(\d+)\s*out\s*of\s*{max_score}',
                rf'(\d+)\s*/\s*{max_score}'
            ]
            
            score = None
            for pattern in score_patterns:
                match = re.search(pattern, response, re.IGNORECASE)
                if match:
                    score = int(match.group(1))
                    break
            
            # If no score found, try to extract just a number
            if score is None:
                number_match = re.search(rf'(\d+)', response)
                if number_match:
                    potential_score = int(number_match.group(1))
                    if 0 <= potential_score <= max_score:
                        score = potential_score
            
            # Default score if nothing found
            if score is None:
                score = max_score // 2  # Default to middle score
            
            # Ensure score is within bounds
            score = max(0, min(score, max_score))
            
            # Convert to letter grade
            if max_score == 50:
                if score >= 45: letter_grade = "A"
                elif score >= 40: letter_grade = "B" 
                elif score >= 35: letter_grade = "C"
                elif score >= 30: letter_grade = "D"
                else: letter_grade = "F"
            elif max_score == 100:
                if score >= 90: letter_grade = "A"
                elif score >= 80: letter_grade = "B"
                elif score >= 70: letter_grade = "C" 
                elif score >= 60: letter_grade = "D"
                else: letter_grade = "F"
            elif max_score == 225:
                if score >= 203: letter_grade = "A"
                elif score >= 180: letter_grade = "B"
                elif score >= 158: letter_grade = "C"
                elif score >= 135: letter_grade = "D"
                else: letter_grade = "F"
            else:
                # Generic percentage-based grading
                percentage = (score / max_score) * 100
                if percentage >= 90: letter_grade = "A"
                elif percentage >= 80: letter_grade = "B"
                elif percentage >= 70: letter_grade = "C"
                elif percentage >= 60: letter_grade = "D"
                else: letter_grade = "F"
            
            # Clean feedback by removing score references
            feedback = response
            for pattern in score_patterns:
                feedback = re.sub(pattern, '', feedback, flags=re.IGNORECASE)
            
            # Remove common score indicators
            feedback = re.sub(r'SCORE:\s*\d+', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'Grade:\s*[A-F]', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'\d+/\d+\s*-?\s*', '', feedback)
            
            # Clean and trim
            feedback = self.clean_feedback(feedback)
            
            return score, letter_grade, feedback
            
        except Exception as e:
            print(f"Error extracting grade: {e}")
            return max_score // 2, "C", "Error occurred during grading processing."
    
    def grade_submission(self, lecture_content: str, questions: str, submission: str, 
                        student_name: str, max_score: int, model_choice: str = "claude-3-5-sonnet-20241022") -> dict:
        """Grade a single student submission"""
        try:
            # Detect assignment type
            question_count = self.detect_question_count(questions)
            
            if question_count > 1:
                assignment_type = f"This assignment contains {question_count} questions. The student's submission should contain responses to all {question_count} questions listed above."
                evaluation_instruction = f"Evaluate the ENTIRE submission as a complete work, considering all {question_count} of the student's responses together when determining the overall score."
            else:
                assignment_type = "This assignment contains one main question or prompt."
                evaluation_instruction = "Evaluate the student's complete response to the assignment."
            
            # Create the prompt
            prompt = f"""You are grading a student assignment. Here are the details:

LECTURE CONTENT AND CONTEXT:
{lecture_content[:3000]}

ASSIGNMENT QUESTIONS:
{questions}

{assignment_type}

COMPLETE STUDENT SUBMISSION (contains answers to all questions above):
{submission}

{evaluation_instruction}

GRADING REQUIREMENTS:
- Provide a numerical score out of {max_score} points
- Start your response with "SCORE: X/{max_score}" where X is the numerical score
- Follow with detailed feedback explaining the grade
- Consider accuracy, depth of analysis, use of lecture material, and writing quality
- Be specific about strengths and areas for improvement

Grade this submission now:"""

            # System message
            system_message = f"You are grading complete student assignments out of {max_score} points. Provide a numerical score and detailed feedback. Evaluate the ENTIRE submission as one complete work."
            
            # Call Claude
            response = self.client.messages.create(
                model=model_choice,
                max_tokens=1500,
                system=system_message,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = response.content[0].text
            score, letter_grade, feedback = self.extract_grade_and_feedback(response_text, max_score)
            
            return {
                'student_name': student_name,
                'score': score,
                'letter_grade': letter_grade,
                'feedback': feedback,
                'max_score': max_score
            }
            
        except Exception as e:
            print(f"Error grading {student_name}: {e}")
            return {
                'student_name': student_name,
                'score': 0,
                'letter_grade': 'ERROR',
                'feedback': f"Error occurred during grading: {str(e)}",
                'max_score': max_score
            }
    
    def grade_all_submissions(self, lecture_content: str, questions: str, 
                            submissions: List, max_score: int, 
                            model_choice: str = "claude-3-5-sonnet-20241022",
                            progress_callback: Optional[Callable] = None,
                            status_callback: Optional[Callable] = None) -> List[dict]:
        """
        Grade all submissions individually with progress updates
        
        Args:
            lecture_content: Course lecture content
            questions: Assignment questions
            submissions: List of (student_name, submission_text) tuples
            max_score: Maximum possible score
            model_choice: Claude model to use
            progress_callback: Function to call with progress updates (0.0 to 1.0)
            status_callback: Function to call with status text updates
        """
        results = []
        total_submissions = len(submissions)
        
        if status_callback:
            status_callback(f"Starting individual grading of {total_submissions} submissions...")
        
        for i, submission_data in enumerate(submissions):
            # Check for stop signal
            try:
                if hasattr(st, 'session_state') and st.session_state.get('stop_processing', False):
                    if status_callback:
                        status_callback(f"Processing stopped by user at submission {i+1}")
                    print(f"Stopping at submission {i+1}")
                    # Add STOPPED entries for remaining submissions
                    for j in range(i, total_submissions):
                        remaining_submission = submissions[j]
                        results.append({
                            'student_name': remaining_submission[0],
                            'score': 0,
                            'letter_grade': 'STOPPED',
                            'feedback': 'Processing was stopped by user',
                            'max_score': max_score
                        })
                    break
            except:
                pass  # Not in Streamlit context
                
            student_name = submission_data[0]
            submission_text = submission_data[1]
            
            if status_callback:
                status_callback(f"Grading {student_name} ({i+1}/{total_submissions})")
            
            print(f"Grading {student_name} ({i+1}/{total_submissions})")
            
            result = self.grade_submission(
                lecture_content, questions, submission_text, 
                student_name, max_score, model_choice
            )
            
            results.append(result)
            
            # Update progress
            progress = (i + 1) / total_submissions
            if progress_callback:
                progress_callback(progress)
            
            print(f"Completed {student_name}: {result['score']}/{max_score} ({result['letter_grade']})")
        
        if status_callback:
            completed_count = len([r for r in results if r['letter_grade'] != 'STOPPED'])
            status_callback(f"Completed grading {completed_count}/{total_submissions} submissions")
        
        return results