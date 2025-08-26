import re
import anthropic
from typing import List, Tuple, Optional
import streamlit as st

class FixedBatchGraderV2:
    def __init__(self, client):
        self.client = client
        self.max_submissions_per_batch = 4  # Smaller batches for better reliability
        
    def clean_feedback(self, feedback: str) -> str:
        """Clean feedback using comprehensive cleaner"""
        try:
            from grade_all import clean_feedback_comprehensive
            return clean_feedback_comprehensive(feedback)
        except ImportError:
            # Fallback if grade_all import fails
            return self._fallback_clean_feedback(feedback)
    
    def _fallback_clean_feedback(self, feedback_text):
        """Fallback feedback cleaner if grade_all import fails"""
        if not feedback_text or not isinstance(feedback_text, str):
            return feedback_text
        
        # Remove various greeting patterns
        greeting_patterns = [
            r'^Dear\s+[^,:\n]*[,:]?\s*',           # Dear Student, Dear John,
            r'^Hello\s+[^,:\n]*[,:]?\s*',          # Hello Student,
            r'^Hi\s+[^,:\n]*[,:]?\s*',             # Hi there,
            r'^Greetings\s*[,:]?\s*',              # Greetings,
            r'^Good\s+\w+\s*[,:]?\s*',             # Good morning,
            r'^\w+\s*,\s*',                        # Student, or Name,
            r'^To\s+[^,:\n]*[,:]?\s*',             # To the student,
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
        
        # Clean up extra whitespace and newlines
        feedback_text = re.sub(r'\n\s*\n', '\n', feedback_text)  # Remove empty lines
        feedback_text = re.sub(r'^\s+', '', feedback_text)        # Remove leading whitespace
        feedback_text = re.sub(r'\s+$', '', feedback_text)        # Remove trailing whitespace
        feedback_text = re.sub(r'\s+', ' ', feedback_text)        # Normalize internal whitespace
        
        # Ensure first letter is capitalized if there's content
        if feedback_text:
            feedback_text = feedback_text[0].upper() + feedback_text[1:] if len(feedback_text) > 1 else feedback_text.upper()
        
        return feedback_text
        
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
    
    def extract_grade_and_feedback(self, response_text: str, max_score: int) -> Tuple[int, str, str]:
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
                match = re.search(pattern, response_text, re.IGNORECASE)
                if match:
                    score = int(match.group(1))
                    break
            
            # If no score found, try to extract just a number
            if score is None:
                number_match = re.search(rf'(\d+)', response_text)
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
            feedback = response_text
            for pattern in score_patterns:
                feedback = re.sub(pattern, '', feedback, flags=re.IGNORECASE)
            
            # Remove common score indicators
            feedback = re.sub(r'SCORE:\s*\d+', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'Grade:\s*[A-F]', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'\d+/\d+\s*-?\s*', '', feedback)
            
            # Clean and trim using comprehensive cleaner - FIRST PASS
            feedback = self.clean_feedback(feedback)
            
            # AGGRESSIVE SECOND PASS - Remove any remaining greetings
            feedback = re.sub(r'^Dear\s+[^,:\n]*[,:]?\s*', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'^Hello\s+[^,:\n]*[,:]?\s*', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'^Hi\s+[^,:\n]*[,:]?\s*', '', feedback, flags=re.IGNORECASE)
            feedback = re.sub(r'^\w+\s*,\s*', '', feedback)  # Remove "Student," or any "Name,"
            
            # Final cleanup
            feedback = feedback.strip()
            if feedback and not feedback[0].isupper():
                feedback = feedback[0].upper() + feedback[1:] if len(feedback) > 1 else feedback.upper()
            
            return score, letter_grade, feedback
            
        except Exception as e:
            print(f"Error extracting grade: {e}")
            return max_score // 2, "C", "Error occurred during grading processing."
    
    def grade_batch_v2(self, lecture_content: str, questions: str, submissions: List, max_score: int) -> List[dict]:
        """Grade a batch of submissions with improved prompt and parsing"""
        try:
            # Get debug mode from session state
            debug_mode = False
            try:
                debug_mode = st.session_state.get('debug_batch', False)
            except:
                pass  # Not in Streamlit context
            
            # Detect question count
            question_count = self.detect_question_count(questions)
            
            # Build comprehensive system message
            if question_count > 1:
                system_message = f"You are grading complete student assignments out of {max_score} points. Each submission contains answers to {question_count} questions. Evaluate each ENTIRE submission as one complete work. Do not provide introductory text, greetings, or explanations - grade each student immediately. Do not use 'Dear Student' or any greetings in your feedback."
            else:
                system_message = f"You are grading complete student assignments out of {max_score} points. Each submission contains one complete response. Evaluate each ENTIRE submission as one complete work. Do not provide introductory text, greetings, or explanations - grade each student immediately. Do not use 'Dear Student' or any greetings in your feedback."
            
            # Build the prompt
            if question_count > 1:
                assignment_context = f"This assignment contains {question_count} questions. Each student's submission contains their responses to ALL {question_count} questions listed below."
                evaluation_instruction = f"Each student submission is a COMPLETE assignment containing {question_count} answers. Read each student's COMPLETE submission (all their answers together) and give ONE overall score based on their complete work across all questions."
            else:
                assignment_context = "This assignment contains one main question or prompt."
                evaluation_instruction = "Each student submission is a COMPLETE response to the assignment. Read each student's ENTIRE submission and give ONE overall score."
            
            prompt = f"""You are grading complete student assignments. {assignment_context}

LECTURE CONTENT AND CONTEXT:
{lecture_content[:3000]}

ASSIGNMENT QUESTIONS:
{questions}

IMPORTANT: {evaluation_instruction}

GRADING REQUIREMENTS:
- Start each student's grade with "STUDENT: [Student Name]"
- Provide a numerical score: "SCORE: X/{max_score}"
- Follow with detailed feedback explaining the grade
- Consider accuracy, depth of analysis, use of lecture material, and writing quality

Here are the complete student submissions to grade:

"""
            
            # Add each submission with clear separation
            for i, submission_data in enumerate(submissions):
                student_name = submission_data[0]
                submission_text = submission_data[1]
                
                # Show more of submission for better context
                submission_preview = submission_text[:1000] if len(submission_text) > 1000 else submission_text
                
                prompt += f"""
STUDENT: {student_name}
COMPLETE SUBMISSION:
{submission_preview}

"""
            
            prompt += f"""
Grade each student's COMPLETE submission above. Provide ONE overall score per student out of {max_score} points.

Format: 
STUDENT: [Name]
SCORE: X/{max_score}
FEEDBACK: [Detailed feedback]

Begin grading now:"""

            if debug_mode:
                print("="*50)
                print("DEBUG: BATCH PROMPT BEING SENT TO CLAUDE")
                print("="*50)
                print(f"System: {system_message}")
                print(f"Prompt length: {len(prompt)} characters")
                print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
                print("="*50)
            
            # Call Claude
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4000,
                system=system_message,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = response.content[0].text
            
            if debug_mode:
                print("DEBUG: CLAUDE'S RESPONSE")
                print("="*50)
                print(response_text)
                print("="*50)
            
            # Parse the results
            results = self.parse_batch_results_v2(response_text, submissions, max_score, debug_mode)
            
            return results
            
        except Exception as e:
            print(f"Error in batch grading: {e}")
            # Return error results for all submissions
            error_results = []
            for submission_data in submissions:
                error_results.append({
                    'student_name': submission_data[0],
                    'score': 0,
                    'letter_grade': 'ERROR',
                    'feedback': f"Batch grading error: {str(e)}",
                    'max_score': max_score
                })
            return error_results
    
    def parse_batch_results_v2(self, response_text: str, submissions: List, max_score: int, debug_mode: bool = False) -> List[dict]:
        """Parse Claude's batch response into individual results with improved logic"""
        results = []
        
        try:
            # Split by "STUDENT:" markers
            student_sections = re.split(r'\bSTUDENT:\s*', response_text, flags=re.IGNORECASE)
            
            if debug_mode:
                print(f"DEBUG: Found {len(student_sections)} sections after splitting")
                for i, section in enumerate(student_sections):
                    print(f"Section {i}: {section[:100]}...")
            
            # Skip the first section if it's just intro text
            if len(student_sections) > 1 and len(student_sections[0]) < 100:
                student_sections = student_sections[1:]
            
            # Check if we have the right number of sections
            if len(student_sections) < len(submissions):
                print(f"WARNING: Expected {len(submissions)} student sections, got {len(student_sections)}")
            
            # Process each student section
            for i, section in enumerate(student_sections):
                if i >= len(submissions):
                    break  # Don't process more sections than we have submissions
                
                student_name = submissions[i][0]  # Get the actual student name
                
                # Extract score and feedback
                score, letter_grade, feedback = self.extract_grade_and_feedback(section, max_score)
                
                # ADDITIONAL CLEANING PASS - Sometimes Claude adds greetings after our initial cleaning
                feedback = re.sub(r'^Dear\s+[^,:\n]*[,:]?\s*', '', feedback, flags=re.IGNORECASE)
                feedback = re.sub(r'^Hello\s+[^,:\n]*[,:]?\s*', '', feedback, flags=re.IGNORECASE)
                feedback = re.sub(r'^Hi\s+[^,:\n]*[,:]?\s*', '', feedback, flags=re.IGNORECASE)
                feedback = re.sub(r'^\w+\s*,\s*', '', feedback)
                feedback = feedback.strip()
                
                # Ensure proper capitalization
                if feedback and not feedback[0].isupper():
                    feedback = feedback[0].upper() + feedback[1:] if len(feedback) > 1 else feedback.upper()
                
                # Check for meta-commentary and skip if found
                if any(phrase in feedback.lower()[:200] for phrase in [
                    "i'll grade", "i will grade", "let me grade", "here are the grades",
                    "grading these", "using the format", "based on the criteria"
                ]):
                    print(f"WARNING: Meta-commentary detected for {student_name}, using fallback")
                    # Try to extract actual feedback from later in the response
                    fallback_feedback = self._extract_fallback_feedback(section, max_score)
                    if fallback_feedback:
                        score, letter_grade, feedback = fallback_feedback
                
                result = {
                    'student_name': student_name,
                    'score': score,
                    'letter_grade': letter_grade,
                    'feedback': feedback,
                    'max_score': max_score
                }
                
                results.append(result)
                
                if debug_mode:
                    print(f"DEBUG: Processed {student_name}: {score}/{max_score} ({letter_grade})")
            
            # Fill in any missing results with errors
            while len(results) < len(submissions):
                missing_student = submissions[len(results)][0]
                results.append({
                    'student_name': missing_student,
                    'score': 0,
                    'letter_grade': 'ERROR',
                    'feedback': 'No response found in batch processing',
                    'max_score': max_score
                })
            
            return results
            
        except Exception as e:
            print(f"Error parsing batch results: {e}")
            # Return error results for all submissions
            error_results = []
            for submission_data in submissions:
                error_results.append({
                    'student_name': submission_data[0],
                    'score': 0,
                    'letter_grade': 'ERROR',
                    'feedback': f"Parsing error: {str(e)}",
                    'max_score': max_score
                })
            return error_results
    
    def _extract_fallback_feedback(self, section: str, max_score: int) -> Optional[Tuple[int, str, str]]:
        """Try to extract actual feedback when meta-commentary is detected"""
        try:
            # Look for patterns that indicate actual grading
            feedback_patterns = [
                r'FEEDBACK:\s*(.+?)(?=\n\n|\n*STUDENT:|$)',
                r'Grade:\s*\d+[./]\d+\s*[-–]?\s*(.+?)(?=\n\n|\n*STUDENT:|$)',
                r'\d+[./]\d+\s*[-–]\s*(.+?)(?=\n\n|\n*STUDENT:|$)',
            ]
            
            for pattern in feedback_patterns:
                match = re.search(pattern, section, re.IGNORECASE | re.DOTALL)
                if match:
                    potential_feedback = match.group(1).strip()
                    if len(potential_feedback) > 50:  # Substantial feedback
                        score, letter_grade, feedback = self.extract_grade_and_feedback(section, max_score)
                        return (score, letter_grade, potential_feedback)
            
            return None
            
        except Exception:
            return None
    
    def grade_all_submissions(self, submissions: List, lecture_content: str, questions: str, max_score: int,
                            progress_callback=None, status_callback=None) -> List[dict]:
        """Grade all submissions using batching with progress tracking"""
        all_results = []
        total_submissions = len(submissions)
        
        if status_callback:
            status_callback(f"Starting batch processing of {total_submissions} submissions...")
        
        # Process in batches
        for i in range(0, total_submissions, self.max_submissions_per_batch):
            batch_end = min(i + self.max_submissions_per_batch, total_submissions)
            batch = submissions[i:batch_end]
            batch_number = i // self.max_submissions_per_batch + 1
            total_batches = (total_submissions + self.max_submissions_per_batch - 1) // self.max_submissions_per_batch
            
            if status_callback:
                status_callback(f"Processing batch {batch_number}/{total_batches} (submissions {i+1}-{batch_end})")
            
            print(f"Processing batch {batch_number}: submissions {i+1}-{batch_end}")
            
            # Check for stop signal
            try:
                if st.session_state.get('stop_processing', False):
                    if status_callback:
                        status_callback(f"Processing stopped by user at batch {batch_number}")
                    print(f"Stopping at batch {batch_number}")
                    
                    # Add STOPPED entries for remaining submissions
                    for j in range(i, total_submissions):
                        if j < len(submissions):
                            remaining_submission = submissions[j]
                            all_results.append({
                                'student_name': remaining_submission[0],
                                'score': 0,
                                'letter_grade': 'STOPPED',
                                'feedback': 'Processing was stopped by user',
                                'max_score': max_score
                            })
                    break
            except:
                pass  # Not in Streamlit context
            
            batch_results = self.grade_batch_v2(lecture_content, questions, batch, max_score)
            all_results.extend(batch_results)
            
            # Update progress
            progress = batch_end / total_submissions
            if progress_callback:
                progress_callback(progress)
        
        if status_callback:
            completed_count = len([r for r in all_results if r.get('letter_grade') != 'STOPPED'])
            status_callback(f"Completed batch processing: {completed_count}/{total_submissions} submissions")
        
        return all_results