"""
Rubric-based assignment handler
Replaces the old question-based system with Canvas rubric integration
"""

from typing import Dict, List, Tuple, Optional
import logging
from canvas_rubric_api import get_rubric_for_assignment
from course_document_processor import CourseDocumentProcessor, load_course_embeddings
import anthropic
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RubricAssignmentHandler:
    """
    Assignment handler that uses Canvas rubrics and course document embeddings
    """

    def __init__(
        self,
        assignment_key: str,
        display_name: str,
        canvas_assignment_id: str,
        course_id: str,
        course_documents_path: str,
        claude_client: anthropic.Anthropic,
    ) -> None:
        """
        Initialize rubric-based assignment handler

        Args:
            assignment_key: Internal key for this assignment type
            display_name: Human-readable name
            canvas_assignment_id: Canvas assignment ID for rubric retrieval
            course_id: Canvas course ID
            course_documents_path: Path to course documents (e.g., "../db/text/HIST109")
            claude_client: Anthropic Claude API client
        """
        self.assignment_key = assignment_key
        self.display_name = display_name
        self.canvas_assignment_id = canvas_assignment_id
        self.course_id = course_id
        self.course_documents_path = course_documents_path
        self.claude_client = claude_client

        # Initialize components
        self.rubric_data: Dict = {}
        self.document_processor: Optional[CourseDocumentProcessor] = None
        self.total_points: float = 0.0
        self.rubric_criteria: List[Dict] = []

        # Load rubric and documents
        self._load_rubric()
        self._load_course_documents()

    def _load_rubric(self) -> None:
        """Load rubric data from Canvas API"""
        try:
            logger.info(f"Loading rubric for assignment {self.canvas_assignment_id}")
            self.rubric_data = get_rubric_for_assignment(
                self.course_id, self.canvas_assignment_id
            )

            if self.rubric_data:
                self.rubric_criteria = self.rubric_data.get("rubric_criteria", [])
                self.total_points = float(self.rubric_data.get("total_points", 100))
                logger.info(
                    f"Loaded rubric with {len(self.rubric_criteria)} criteria, {self.total_points} total points"
                )
            else:
                logger.warning(
                    f"No rubric found for assignment {self.canvas_assignment_id}"
                )
                self.total_points = 100.0  # Default fallback

        except Exception as e:
            logger.error(f"Error loading rubric: {e}")
            self.total_points = 100.0  # Default fallback

    def _load_course_documents(self) -> None:
        """Load course document embeddings"""
        try:
            logger.info(f"Loading course documents from {self.course_documents_path}")
            course_name = self.assignment_key  # Use assignment key as course identifier
            self.document_processor = load_course_embeddings(
                self.course_documents_path, course_name, force_refresh=False
            )

            if self.document_processor and getattr(self.document_processor, "df", None) is not None:
                stats = self.document_processor.get_course_statistics()
                logger.info(f"Loaded {stats.get('total_chunks', 0)} document chunks")
            else:
                logger.warning("No course documents loaded")

        except Exception as e:
            logger.error(f"Error loading course documents: {e}")

    def get_rubric_prompt(self) -> str:
        """Generate rubric description for grading prompts"""
        if not self.rubric_criteria:
            return (
                f"Grade this submission out of {int(self.total_points)} points based on "
                "quality, accuracy, and completeness."
            )

        return self.rubric_data.get("formatted_rubric", "")

    def search_relevant_documents(
        self, submission_text: str, query_terms: Optional[List[str]] = None
    ) -> str:
        """Search for relevant course documents based on submission content"""
        if not self.document_processor:
            return "No course documents available for context."

        # Use submission text as primary query
        primary_context = self.document_processor.get_document_context(submission_text)

        # If additional query terms provided, search for those too
        if query_terms:
            additional_context_parts: List[str] = []
            for term in query_terms:
                if term and str(term).strip():  # Ensure term is not None or empty
                    term_context = self.document_processor.get_document_context(
                        str(term), max_context_length=500
                    )
                    if "No relevant course documents" not in term_context:
                        additional_context_parts.append(term_context)

            if additional_context_parts:
                additional_context = "\n".join(additional_context_parts)
                return f"{primary_context}\n\nADDITIONAL CONTEXT:\n{additional_context}"

        return primary_context

    def create_grading_prompt(
        self, submission_text: str, student_name: str, additional_context: str = ""
    ) -> str:
        """Create comprehensive grading prompt using rubric and course documents"""

        # Ensure all parameters are valid strings
        if not submission_text or not isinstance(submission_text, str):
            submission_text = "No submission text provided"

        if not student_name or not isinstance(student_name, str):
            student_name = "Unknown Student"

        if not isinstance(additional_context, str):
            additional_context = ""

        # Get relevant course materials - pass empty list instead of None
        document_context = self.search_relevant_documents(submission_text, [])

        # Build the grading prompt
        prompt = f"""You are grading a student assignment using a specific rubric and course materials as context.

STUDENT: {student_name}

{self.get_rubric_prompt()}

{document_context}

{additional_context if additional_context else ""}

STUDENT SUBMISSION:
{submission_text}

GRADING INSTRUCTIONS:
1. Evaluate the submission against each rubric criterion
2. Use the course materials as context to assess accuracy and depth
3. For each criterion, provide:
   - Points earned out of possible points
   - Specific justification based on rubric standards
   - Reference to course materials when relevant
4. Provide constructive feedback for improvement

Format your response as:
CRITERION 1: [earned_points]/[max_points] - [detailed justification]
CRITERION 2: [earned_points]/[max_points] - [detailed justification]
...
TOTAL SCORE: [total_earned]/[total_possible]
OVERALL FEEDBACK: [comprehensive feedback and suggestions for improvement]
"""
        return prompt

    def grade_submission(
        self, submission_text: str, student_name: str, model: str = "claude-3-5-sonnet-20241022"
    ) -> Dict:
        """Grade a single submission using rubric and course documents"""
        try:
            # Create grading prompt
            prompt = self.create_grading_prompt(submission_text, student_name)

            # Call Claude API
            response = self.claude_client.messages.create(
                model=model,
                max_tokens=2000,
                system=(
                    "You are an expert grader using a detailed rubric. "
                    "Provide specific, constructive feedback based on the rubric criteria and course materials."
                ),
                messages=[{"role": "user", "content": prompt}],
            )

            # Handle the response content safely
            response_text = ""
            if hasattr(response, "content") and response.content:
                for content_block in response.content:
                    if getattr(content_block, "type", None) == "text":
                        text = getattr(content_block, "text", "")
                        if text:
                            response_text += text

            if not response_text:
                response_text = "Unable to generate response"

            # Parse the response
            result = self._parse_grading_response(response_text, student_name)
            return result

        except Exception as e:
            logger.error(f"Error grading submission for {student_name}: {e}")
            return {
                "student_name": student_name,
                "score": 0,
                "max_score": int(self.total_points),
                "letter_grade": "ERROR",
                "feedback": f"Error occurred during grading: {str(e)}",
                "criterion_scores": [],
                "raw_response": "",
            }

    def _parse_grading_response(self, response_text: str, student_name: str) -> Dict:
        """Parse Claude's grading response into structured data"""
        try:
            # Initialize result
            result: Dict = {
                "student_name": student_name,
                "score": 0.0,
                "max_score": float(self.total_points),
                "letter_grade": "F",
                "feedback": response_text,
                "criterion_scores": [],
                "raw_response": response_text,
            }

            # Extract total score
            total_match = re.search(
                r"TOTAL SCORE:\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
                response_text,
                re.IGNORECASE,
            )
            if total_match:
                result["score"] = float(total_match.group(1))
                result["max_score"] = float(total_match.group(2))
            else:
                # Try to sum individual criterion scores
                criterion_matches = re.findall(
                    r"CRITERION \d+:\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
                    response_text,
                    re.IGNORECASE,
                )
                if criterion_matches:
                    total_earned = sum(float(match[0]) for match in criterion_matches)
                    total_possible = sum(float(match[1]) for match in criterion_matches)
                    result["score"] = total_earned
                    result["max_score"] = total_possible

            # Convert to letter grade
            if result["max_score"] > 0:
                percentage = (result["score"] / result["max_score"]) * 100.0
                if percentage >= 90:
                    result["letter_grade"] = "A"
                elif percentage >= 80:
                    result["letter_grade"] = "B"
                elif percentage >= 70:
                    result["letter_grade"] = "C"
                elif percentage >= 60:
                    result["letter_grade"] = "D"
                else:
                    result["letter_grade"] = "F"

            # Extract individual criterion scores and justifications
            criterion_pattern = (
                r"CRITERION (\d+):\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\s*-\s*"
                r"([^C]*?(?=CRITERION \d+:|TOTAL SCORE:|OVERALL FEEDBACK:|$))"
            )
            criterion_matches = re.findall(
                criterion_pattern, response_text, re.IGNORECASE | re.DOTALL
            )

            for match in criterion_matches:
                criterion_num, earned, possible, justification = match
                result["criterion_scores"].append(
                    {
                        "criterion": int(criterion_num),
                        "earned": float(earned),
                        "possible": float(possible),
                        "justification": justification.strip(),
                    }
                )

            # Extract overall feedback (grab everything until end)
            overall_feedback_match = re.search(
                r"OVERALL FEEDBACK:\s*(.+)$", response_text, re.IGNORECASE | re.DOTALL
            )
            if overall_feedback_match:
                overall_feedback = overall_feedback_match.group(1).strip()
                # Clean up the feedback - remove score tokens already captured
                cleaned_feedback = re.sub(r"\b\d+/\d+\b\s*-?\s*", "", overall_feedback)
                result["feedback"] = cleaned_feedback

            return result

        except Exception as e:
            logger.error(f"Error parsing grading response for {student_name}: {e}")
            # Return basic result with raw response
            return {
                "student_name": student_name,
                "score": float(self.total_points) / 2.0,  # Default to middle score
                "max_score": float(self.total_points),
                "letter_grade": "C",
                "feedback": response_text,
                "criterion_scores": [],
                "raw_response": response_text,
            }

    def batch_grade_submissions(
        self,
        submissions: List[Tuple[str, str]],
        model: str = "claude-3-5-sonnet-20241022",
        progress_callback=None,
    ) -> List[Dict]:
        """Grade multiple submissions"""
        results: List[Dict] = []
        total = len(submissions) if submissions else 0

        for i, (student_name, submission_text) in enumerate(submissions or []):
            if progress_callback and total:
                progress_callback(i / total, f"Grading {student_name} ({i+1}/{total})")

            result = self.grade_submission(submission_text, student_name, model)
            results.append(result)

            logger.info(
                f"Graded {student_name}: {result['score']}/{result['max_score']} ({result['letter_grade']})"
            )

        if progress_callback:
            progress_callback(1.0, "Grading complete")

        return results

    def get_assignment_info(self) -> Dict:
        """Get comprehensive assignment information"""
        info: Dict = {
            "assignment_key": self.assignment_key,
            "display_name": self.display_name,
            "canvas_assignment_id": self.canvas_assignment_id,
            "course_id": self.course_id,
            "total_points": int(self.total_points),
            "has_rubric": len(self.rubric_criteria) > 0,
            "criterion_count": len(self.rubric_criteria),
            "has_course_documents": bool(
                self.document_processor is not None
                and getattr(self.document_processor, "df", None) is not None
            ),
        }

        # Add document statistics
        if self.document_processor:
            doc_stats = self.document_processor.get_course_statistics()
            info.update(
                {
                    "document_chunks": doc_stats.get("total_chunks", 0),
                    "unique_documents": doc_stats.get("unique_documents", 0),
                    "document_types": doc_stats.get("document_types", {}),
                }
            )

        # Add rubric details
        if self.rubric_criteria:
            info["rubric_criteria"] = [
                {
                    "description": criterion.get("description", ""),
                    "points": criterion.get("points", 0),
                    "rating_levels": len(criterion.get("ratings", [])),
                }
                for criterion in self.rubric_criteria
            ]

        return info

    def refresh_embeddings(self) -> None:
        """Refresh course document embeddings"""
        if self.document_processor:
            course_name = self.assignment_key
            self.document_processor.load_or_create_embeddings(
                course_name, force_refresh=True
            )
            logger.info(f"Refreshed embeddings for {self.assignment_key}")

    def validate_configuration(self) -> List[str]:
        """Validate assignment handler configuration"""
        messages: List[str] = []

        # Check rubric
        if not self.rubric_data or not self.rubric_criteria:
            messages.append(
                f"Warning: No rubric found for assignment {self.canvas_assignment_id}"
            )
        elif self.total_points <= 0:
            messages.append(
                f"Error: Invalid total points for {self.assignment_key}: {self.total_points}"
            )

        # Check course documents
        if (
            not self.document_processor
            or getattr(self.document_processor, "df", None) is None
            or len(getattr(self.document_processor, "df", [])) == 0
        ):
            messages.append(
                f"Warning: No course documents loaded for {self.assignment_key}"
            )

        # Check Canvas API configuration
        try:
            from canvas_rubric_api import load_canvas_credentials

            load_canvas_credentials()
        except Exception as e:
            messages.append(f"Error: Canvas API configuration issue: {e}")

        return messages

    def __str__(self) -> str:
        return (
            f"{self.display_name} ({self.assignment_key}): "
            f"{int(self.total_points)} points, {len(self.rubric_criteria)} criteria"
        )

    def __repr__(self) -> str:
        return f"<RubricAssignmentHandler assignment_key={self.assignment_key!r}>"
