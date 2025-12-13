"""
Rubric-based assignment handler
Replaces the old question-based system with Canvas rubric integration
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
import logging
import re

from canvas_rubric_api import get_rubric_for_assignment
from course_document_processor import CourseDocumentProcessor, load_course_embeddings

# Unified LLM interface (provider-agnostic)
from llm_provider import LLMBase, AnthropicLLM  # AnthropicLLM only for backward-compat

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
        # NEW: prefer llm, keep claude_client for backward compatibility
        llm: Optional[LLMBase] = None,
        claude_client: Optional[Any] = None,  # legacy raw Anthropic client
        leniency_multiplier: float = 1.0,  # NEW: configurable grading leniency
    ) -> None:
        """
        Initialize rubric-based assignment handler

        Args:
            assignment_key: Internal key for this assignment type
            display_name: Human-readable name
            canvas_assignment_id: Canvas assignment ID for rubric retrieval
            course_id: Canvas course ID
            course_documents_path: Path to course documents (e.g., "../db/text/HIST109")
            llm: Unified LLM wrapper (recommended). See llm_provider.LLMBase
            claude_client: (deprecated) Raw Anthropic client; wrapped if llm not supplied
            leniency_multiplier: Multiplier for scores (1.0=no change, 1.2=20% boost, 0.9=10% stricter)
        """
        self.assignment_key = assignment_key
        self.display_name = display_name
        self.canvas_assignment_id = canvas_assignment_id
        self.course_id = course_id
        self.course_documents_path = course_documents_path
        self.leniency_multiplier = leniency_multiplier

        # Provider-agnostic LLM setup
        if llm is not None:
            self.llm = llm
        elif claude_client is not None:
            # Allow old call-sites to keep working by wrapping Anthropic client
            self.llm = AnthropicLLM(claude_client)
        else:
            raise ValueError("You must provide either llm= (preferred) or claude_client= (deprecated).")

        # Initialize components
        self.rubric_data: Dict[str, Any] = {}
        self.document_processor: Optional[CourseDocumentProcessor] = None
        self.total_points: float = 0.0
        self.rubric_criteria: List[Dict[str, Any]] = []

        # Load rubric and documents
        self._load_rubric()
        self._load_course_documents()

    # ──────────────────────────────────────────────────────────────────────
    # Loading / setup
    # ──────────────────────────────────────────────────────────────────────
    def _load_rubric(self) -> None:
        """Load rubric data from Canvas API"""
        try:
            logger.info(f"Loading rubric for assignment {self.canvas_assignment_id}")
            self.rubric_data = get_rubric_for_assignment(self.course_id, self.canvas_assignment_id)

            if self.rubric_data:
                self.rubric_criteria = self.rubric_data.get("rubric_criteria", [])
                self.total_points = float(self.rubric_data.get("total_points", 100))
                
                # Check if rubric criteria points need scaling
                rubric_sum = sum(float(c.get("points", 0)) for c in self.rubric_criteria)
                if rubric_sum > 0 and abs(rubric_sum - self.total_points) > 0.01:
                    # Rubric criteria sum doesn't match assignment total - need to scale
                    scale_factor = self.total_points / rubric_sum
                    logger.info(f"Scaling rubric criteria: {rubric_sum} points -> {self.total_points} points (factor: {scale_factor:.3f})")
                    
                    # Scale all criterion points and rating points
                    for criterion in self.rubric_criteria:
                        original_points = float(criterion.get("points", 0))
                        criterion["points"] = original_points * scale_factor
                        criterion["original_points"] = original_points  # Keep original for reference
                        
                        # Scale rating points too
                        for rating in criterion.get("ratings", []):
                            original_rating_points = float(rating.get("points", 0))
                            rating["points"] = original_rating_points * scale_factor
                            rating["original_points"] = original_rating_points
                
                logger.info(
                    f"Loaded rubric with {len(self.rubric_criteria)} criteria, {self.total_points} total points"
                )
            else:
                logger.warning(f"No rubric found for assignment {self.canvas_assignment_id}")
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

    # ──────────────────────────────────────────────────────────────────────
    # Prompt construction
    # ──────────────────────────────────────────────────────────────────────
    def get_rubric_prompt(self) -> str:
        """Generate rubric description for grading prompts"""
        if not self.rubric_criteria:
            return (
                f"Grade this submission out of {int(self.total_points)} points based on "
                "quality, accuracy, and completeness."
            )
        
        # Format rubric with (possibly scaled) criterion points
        rubric_text = f"Grade this submission out of {self.total_points} total points using the following rubric:\n\n"
        
        for i, criterion in enumerate(self.rubric_criteria, 1):
            points = criterion.get('points', 0)
            desc = criterion.get('description', 'Criterion')
            rubric_text += f"CRITERION {i}: {desc} ({points:.1f} points)\n"
            
            long_desc = criterion.get('long_description', '')
            if long_desc:
                rubric_text += f"Details: {long_desc}\n"
            
            rubric_text += "Rating Scale:\n"
            for rating in criterion.get('ratings', []):
                rating_desc = rating.get('description', '')
                rating_points = rating.get('points', 0)
                rubric_text += f"  - {rating_desc} ({rating_points:.1f} pts)"
                rating_long = rating.get('long_description', '')
                if rating_long:
                    rubric_text += f": {rating_long}"
                rubric_text += "\n"
            
            rubric_text += "\n"
        
        return rubric_text

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

        # SAFE: compute document_context with a fallback
        try:
            document_context = self.search_relevant_documents(submission_text, [])
        except Exception:
            document_context = "No course documents available for context."

        prompt = f"""You are grading {student_name}'s assignment using a specific rubric and course materials as context.

    STYLE:
    - Write in a friendly, encouraging tone
    - Address {student_name} directly using "you" and "your"
    - Be supportive and recognize effort
    - Keep feedback brief and to the point

    GRADING APPROACH:
    - Be generous in your interpretation of the rubric
    - Give credit for partial understanding and effort
    - Focus on what the student did well
    - When work shows understanding of concepts, award full or near-full points

    {self.get_rubric_prompt()}

    {document_context}

    {additional_context if additional_context else ""}

    {student_name}'s SUBMISSION:
    {submission_text}

    GRADING INSTRUCTIONS:
    IMPORTANT: This assignment is worth {self.total_points} TOTAL POINTS. Calculate the total score out of {self.total_points}.
    
    1. Evaluate the submission generously against each rubric criterion
    2. Award full points when work demonstrates understanding, even if not perfectly expressed
    3. For each criterion, provide BRIEF justification (1-2 sentences max)
    4. Keep overall feedback concise (2-3 sentences)

    Format your response as:
    CRITERION 1: [earned_points]/[max_points] - [brief justification]
    CRITERION 2: [earned_points]/[max_points] - [brief justification]
    ...
    TOTAL SCORE: [total_earned]/{self.total_points}
    OVERALL FEEDBACK: [brief, encouraging feedback addressing {student_name} directly]
    """
        return prompt


    def build_system_prompt(self) -> str:
        """Short system directive to keep the LLM on task and format."""
        return (
            "You are a supportive, generous grader. Interpret rubrics generously and give students the benefit of the doubt. "
            "Write in a friendly, encouraging tone addressing the student directly. "
            "Keep feedback brief and focused on positives. "
            "Use the exact output format requested."
        )


    # ──────────────────────────────────────────────────────────────────────
    # Grading
    # ──────────────────────────────────────────────────────────────────────
    def grade_submission(self, submission_text: str, student_name: str, model: str) -> Dict[str, Any]:
        """
        Use the selected LLM to grade a single submission and return a result dict:
        { 'student_name', 'score', 'max_score', 'letter_grade', 'feedback', 'criterion_scores', 'raw_response' }
        """
        try:
            system_prompt = self.build_system_prompt()
            user_prompt = self.create_grading_prompt(submission_text, student_name)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ]

            reply_text = self.llm.generate(
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=2048,
            )

            # DEBUG: Print raw response
            logger.info(f"RAW AI RESPONSE for {student_name}:")
            logger.info(reply_text)
            logger.info("=" * 80)


            return self._parse_grading_response(reply_text, student_name)

        except Exception as e:
            logger.error(f"Error grading submission for {student_name}: {e}")
            return {
                "student_name": student_name,
                "score": 0.0,
                "max_score": float(self.total_points),
                "letter_grade": "ERROR",
                "feedback": f"Error occurred during grading: {str(e)}",
                "criterion_scores": [],
                "raw_response": "",
            }

    # Keep a simple wrapper in case other code expects this name.
    def parse_model_reply(self, reply_text: str, student_name: str) -> Dict[str, Any]:
        return self._parse_grading_response(reply_text, student_name)

    def _parse_grading_response(self, response_text: str, student_name: str) -> Dict[str, Any]:
        """Parse model grading response into structured data"""
        try:
            # Initialize result WITHOUT letter_grade
            result: Dict[str, Any] = {
                "student_name": student_name,
                "score": 0.0,
                "max_score": float(self.total_points),
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
                # Always use self.total_points, not what the LLM said
                # (LLM might mistakenly use 100 even though we told it to use 50)
                result["max_score"] = float(self.total_points)
            else:
                # Try to sum individual criterion scores
                criterion_matches = re.findall(
                    r"CRITERION \d+:\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
                    response_text,
                    re.IGNORECASE,
                )
                if criterion_matches:
                    total_earned = sum(float(match[0]) for match in criterion_matches)
                    # Use self.total_points as max, not sum of criterion max values
                    result["score"] = total_earned
                    result["max_score"] = float(self.total_points)

            # Extract individual criterion scores and justifications
            criterion_pattern = r"CRITERION (\d+):\s*(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\s*-\s*(.+?)(?=(?:CRITERION \d+:|TOTAL SCORE:|OVERALL FEEDBACK:|$))"
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

            # Apply leniency boost to all scores (cap at maximum)
            # Multiplier is configurable via constructor (default 1.0 = no adjustment)
            
            # Boost total score (cap at max)
            if result["score"] > 0 and self.leniency_multiplier != 1.0:
                original_score = result["score"]
                boosted_score = result["score"] * self.leniency_multiplier
                result["score"] = min(boosted_score, result["max_score"])
                boost_pct = (self.leniency_multiplier - 1.0) * 100
                logger.info(f"Applied {boost_pct:+.0f}% leniency to {student_name}: {original_score:.1f} → {result['score']:.1f}/{result['max_score']}")
            
            # Boost individual criterion scores (cap at each criterion's max)
            if self.leniency_multiplier != 1.0:
                for cs in result["criterion_scores"]:
                    if cs["earned"] > 0:
                        boosted = cs["earned"] * self.leniency_multiplier
                        cs["earned"] = min(boosted, cs["possible"])


            # Extract overall feedback
            overall_feedback_match = re.search(
                r"OVERALL FEEDBACK:\s*(.+)$", response_text, re.IGNORECASE | re.DOTALL
            )
            
            # Check if we successfully extracted a score
            if result["score"] == 0.0 and not result["criterion_scores"]:
                logger.warning(f"Could not extract score from response for {student_name}. Response may be malformed.")
                logger.warning(f"Response text: {response_text[:500]}...")
            
            if overall_feedback_match:
                overall_feedback = overall_feedback_match.group(1).strip()
                # Clean up the feedback - remove ALL score tokens (including ones already in the text)
                cleaned_feedback = re.sub(r"\b\d+(?:\.\d+)?/\d+(?:\.\d+)?\b\s*-?\s*", "", overall_feedback)
                # Keep personalized language - don't normalize references
                
                # Only format if we haven't already formatted (avoid duplication)
                if not cleaned_feedback.startswith(f"{result['score']}/{result['max_score']}"):
                    # Format feedback WITH criterion breakdown (no letter grade)
                    breakdown_str = ", ".join([f"{cs['earned']:.1f}" for cs in result["criterion_scores"]])
                    if breakdown_str:
                        result["feedback"] = f"{result['score']:.1f}/{result['max_score']} ({breakdown_str}) - {cleaned_feedback}"
                    else:
                        result["feedback"] = f"{result['score']:.1f}/{result['max_score']} - {cleaned_feedback}"
                else:
                    # Already formatted, just use cleaned version
                    result["feedback"] = cleaned_feedback
            else:
                # No overall feedback found, just use score and breakdown
                breakdown_str = ", ".join([f"{cs['earned']:.1f}" for cs in result["criterion_scores"]])
                if breakdown_str:
                    result["feedback"] = f"{result['score']:.1f}/{result['max_score']} ({breakdown_str}) - No detailed feedback provided"
                else:
                    result["feedback"] = f"{result['score']:.1f}/{result['max_score']} - No detailed feedback provided"

            # Keep personalized language in criterion justifications - don't normalize

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
        
    def _normalize_references(self, text: str) -> str:
        """Normalize person-centric phrasing to document-centric phrasing."""
        import re
        if not isinstance(text, str) or not text:
            return text

        # Handle 'the student' / 'this student'
        text = re.sub(r"\b[Tt]he student\b", "the submission", text)
        text = re.sub(r"\b[Tt]his student\b", "this submission", text)

        # Possessives: student's / student’s  → submission's
        text = re.sub(r"\b[Ss]tudent['’]s\b", "submission's", text)

        # Common variants
        text = re.sub(r"\b[Ss]tudent work\b", "the submission", text)

        # (Optional) soften accidental second-person (conservative)
        # text = re.sub(r"\b[Yy]our\b", "the submission's", text)
        # text = re.sub(r"\b[Yy]ou\b", "the submission", text)

        return text
    
    # ──────────────────────────────────────────────────────────────────────
    # Batch grading & info
    # ──────────────────────────────────────────────────────────────────────
    def batch_grade_submissions(
        self,
        submissions: List[Tuple[str, str]],
        model: str = "claude-3-5-sonnet-20241022",
        progress_callback=None,
    ) -> List[Dict[str, Any]]:
        """Grade multiple submissions"""
        results: List[Dict[str, Any]] = []
        total = len(submissions) if submissions else 0

        for i, (student_name, submission_text) in enumerate(submissions or []):
            if progress_callback and total:
                progress_callback(i / total, f"Grading {student_name} ({i+1}/{total})")

            result = self.grade_submission(submission_text, student_name, model)
            results.append(result)

            logger.info(
                f"Graded {student_name}: {result['score']}/{result['max_score']})"
            )

        if progress_callback:
            progress_callback(1.0, "Grading complete")

        return results

    def get_assignment_info(self) -> Dict[str, Any]:
        """Get comprehensive assignment information"""
        info: Dict[str, Any] = {
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
            self.document_processor.load_or_create_embeddings(course_name, force_refresh=True)
            logger.info(f"Refreshed embeddings for {self.assignment_key}")

    def validate_configuration(self) -> List[str]:
        """Validate assignment handler configuration"""
        messages: List[str] = []

        # Check rubric
        if not self.rubric_data or not self.rubric_criteria:
            messages.append(f"Warning: No rubric found for assignment {self.canvas_assignment_id}")
        elif self.total_points <= 0:
            messages.append(f"Error: Invalid total points for {self.assignment_key}: {self.total_points}")

        # Check course documents
        if (
            not self.document_processor
            or getattr(self.document_processor, "df", None) is None
            or len(getattr(self.document_processor, "df", [])) == 0
        ):
            messages.append(f"Warning: No course documents loaded for {self.assignment_key}")

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