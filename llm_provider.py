# llm_provider.py
from __future__ import annotations
from typing import List, Dict, Any

# We accept OpenAI-style messages:
# [{"role": "system"|"user"|"assistant", "content": "..."}, ...]

class LLMBase:
    def generate(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        raise NotImplementedError

# ── Anthropic wrapper ─────────────────────────────────────────────────────────
class AnthropicLLM(LLMBase):
    def __init__(self, client):
        self.client = client  # anthropic.Anthropic

    def generate(self, model, messages, temperature=0.2, max_tokens=1024) -> str:
        system = ""
        converted = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system = m.get("content", "") or system
            elif role in ("user", "assistant"):
                converted.append({"role": role, "content": m.get("content", "")})

        resp = self.client.messages.create(
            model=model,
            system=system or None,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=converted,
        )
        # Anthropic returns a list of content blocks; take the first text block
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""

# ── OpenAI wrapper ────────────────────────────────────────────────────────────
class OpenAILLM(LLMBase):
    def __init__(self, client):
        self.client = client  # OpenAI() instance

    def _fallback_family(self, model: str) -> str:
        # If model looks like a snapshot, e.g. "gpt-4o-2024-11-20",
        # fall back to the family "gpt-4o".
        if "-" in model:
            # first two tokens usually identify the family (gpt-4o, gpt-4.1, etc.)
            parts = model.split("-")
            if len(parts) >= 2:
                return "-".join(parts[:2])  # e.g., "gpt-4o"
        return model

    def generate(self, model, messages, temperature=0.2, max_tokens=1024) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )
            return resp.choices[0].message.content

        except Exception as e:
            # If the model isn't found, try the family alias once.
            txt = str(e).lower()
            if "not_found" in txt or "not found" in txt or "404" in txt:
                fam = self._fallback_family(model)
                if fam != model:
                    resp = self.client.chat.completions.create(
                        model=fam,
                        messages=messages,
                        temperature=temperature,
                        max_completion_tokens=max_tokens,
                    )
                    return resp.choices[0].message.content
            raise

# ── Google (Gemini) wrapper ───────────────────────────────────────────────────
class GoogleLLM(LLMBase):
    def __init__(self, client):
        self.client = client  # genai.Client from google-genai

    def _to_gemini(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        # Convert OpenAI-style messages into Gemini contents
        lines = []
        system = ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system = content
            else:
                lines.append(f"{role.upper()}: {content}")
        prompt = ""
        if system:
            prompt += f"SYSTEM: {system}\n\n"
        prompt += "\n".join(lines)
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}

    def _extract_text(self, resp: Any) -> str:
        # Be liberal in what we accept; google-genai responses expose text in a few ways
        import logging
        logger = logging.getLogger(__name__)
        
        # Method 1: Direct .text attribute
        if hasattr(resp, "text") and resp.text:
            logger.debug("Extracted text using resp.text")
            return resp.text
        
        # Method 2: output_text attribute
        if hasattr(resp, "output_text") and resp.output_text:
            logger.debug("Extracted text using resp.output_text")
            return resp.output_text
        
        # Method 3: candidates[0].content.parts[0].text
        try:
            candidates = getattr(resp, "candidates", None)
            if candidates and len(candidates) > 0:
                candidate = candidates[0]
                content = getattr(candidate, "content", None)
                if content:
                    parts = getattr(content, "parts", None)
                    if parts and len(parts) > 0:
                        # Collect all text from all parts
                        texts = []
                        for part in parts:
                            if hasattr(part, "text"):
                                texts.append(part.text)
                        if texts:
                            result = "".join(texts)
                            logger.debug(f"Extracted {len(result)} chars from content.parts")
                            return result
                    else:
                        logger.warning(f"Content has no parts or empty parts. Content: {content}")
        except Exception as e:
            logger.warning(f"Method 3 (candidates extraction) failed: {e}")
        
        # Method 4: Try accessing via dict-like interface
        try:
            if hasattr(resp, "candidates") and resp.candidates:
                candidate = resp.candidates[0]
                if hasattr(candidate, "content") and candidate.content:
                    if hasattr(candidate.content, "parts") and candidate.content.parts:
                        texts = []
                        for part in candidate.content.parts:
                            if hasattr(part, "text") and part.text:
                                texts.append(part.text)
                        if texts:
                            result = "".join(texts)
                            logger.debug(f"Extracted {len(result)} chars using dict-like access")
                            return result
        except Exception as e:
            logger.debug(f"Method 4 failed: {e}")
        
        # Log what we got for debugging
        logger.error(f"Could not extract text from Google response. Response type: {type(resp)}")
        if hasattr(resp, "candidates") and resp.candidates:
            cand = resp.candidates[0]
            logger.error(f"Candidate finish_reason: {getattr(cand, 'finish_reason', 'unknown')}")
            if hasattr(cand, "content"):
                logger.error(f"Content type: {type(cand.content)}")
                logger.error(f"Content dir: {[a for a in dir(cand.content) if not a.startswith('_')]}")
        
        return ""

    def generate(self, model, messages, temperature=0.2, max_tokens=1024) -> str:
        import logging
        logger = logging.getLogger(__name__)
        
        req = self._to_gemini(messages)
        contents = req["contents"]

        try:
            # google-genai signature uses `config=...`
            resp = self.client.models.generate_content(
                model=model,
                contents=contents,
                config={  # <- not 'generation_config'
                    "temperature": float(temperature),
                    "max_output_tokens": int(max_tokens),
                },
            )
            logger.debug(f"Google API response received, type: {type(resp)}")
        except TypeError as e:
            logger.warning(f"Config parameter failed, trying without config: {e}")
            # Fallback: call without config if the installed version is stricter
            resp = self.client.models.generate_content(
                model=model,
                contents=contents,
            )

        text = self._extract_text(resp)
        if not text:
            logger.error("Failed to extract text from Google response")
        return text

# ── Factory ───────────────────────────────────────────────────────────────────
def make_llm(provider: str, raw_client) -> LLMBase:
    p = provider.lower()
    if p == "anthropic":
        return AnthropicLLM(raw_client)
    if p == "openai":
        return OpenAILLM(raw_client)
    if p == "google":
        return GoogleLLM(raw_client)
    raise ValueError(f"Unknown provider: {provider}")