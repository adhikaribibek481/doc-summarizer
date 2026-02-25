"""
Dual-model summarization with automatic fallback
Primary: sshleifer/distilbart-cnn-12-6 (local, CPU)
Fallback: llama-3.1-8b-instant via Groq API

Improvements over base version:
- Uses distilbart-cnn-12-6 (deeper model, better coherence)
- Summary-of-summaries: chunk summaries are re-summarized for a clean final output
- Sentence deduplication: removes repeated sentences across chunks
"""

import os
import re
import logging
from typing import Optional

import torch
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

LOCAL_MODEL_NAME = "sshleifer/distilbart-cnn-12-6"
GROQ_MODEL_NAME  = "llama-3.1-8b-instant"
MAX_CHUNK_CHARS  = 3000   # safe chars per chunk for distilbart
MAX_INPUT_CHARS  = 12000  # safe chars before passing to Groq

_local_model     = None
_local_tokenizer = None


def _chunk_text(text: str, chunk_size: int = MAX_CHUNK_CHARS) -> list[str]:
    paragraphs = text.split("\n")
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) < chunk_size:
            current += para + "\n"
        else:
            if current.strip():
                chunks.append(current.strip())
            current = para + "\n"
    if current.strip():
        chunks.append(current.strip())

    final_chunks = []
    for chunk in chunks:
        if len(chunk) > chunk_size:
            for i in range(0, len(chunk), chunk_size):
                final_chunks.append(chunk[i : i + chunk_size])
        else:
            final_chunks.append(chunk)
    return final_chunks or [text[:chunk_size]]


def _deduplicate_sentences(text: str) -> str:
    """Remove duplicate or near-duplicate sentences from the summary."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    seen, unique = set(), []
    for s in sentences:
        normalized = re.sub(r'\s+', ' ', s.lower().strip())
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(s.strip())
    return " ".join(unique)


def _get_local_model():
    global _local_model, _local_tokenizer
    if _local_model is None:
        from transformers import BartForConditionalGeneration, BartTokenizer
        logger.info(f"Loading local model: {LOCAL_MODEL_NAME} ...")
        _local_tokenizer = BartTokenizer.from_pretrained(LOCAL_MODEL_NAME)
        _local_model     = BartForConditionalGeneration.from_pretrained(LOCAL_MODEL_NAME)
        _local_model.eval()
        logger.info("Local model loaded successfully.")
    return _local_model, _local_tokenizer


def _summarize_text(text: str, max_out: int = 150, min_out: int = 40) -> str:
    """Run one distilBART summarization pass on text."""
    model, tokenizer = _get_local_model()
    inputs = tokenizer(text, max_length=1024, truncation=True, return_tensors="pt")
    with torch.no_grad():
        ids = model.generate(
            inputs["input_ids"],
            max_length=max_out,
            min_length=min_out,
            length_penalty=2.0,
            num_beams=4,
            early_stopping=True,
            no_repeat_ngram_size=3,   # prevents repeating 3-grams
        )
    return tokenizer.decode(ids[0], skip_special_tokens=True)


def _try_local(text: str) -> str:
    """
    Summarize using local distilBART with summary-of-summaries:
    1. Summarize each chunk individually
    2. Deduplicate sentences across chunk summaries
    3. If multiple chunks, do a final summarization pass for coherence
    """
    chunks    = _chunk_text(text, MAX_CHUNK_CHARS)
    summaries = []

    for chunk in chunks:
        if len(chunk.strip()) < 50:
            continue
        summaries.append(_summarize_text(chunk, max_out=150, min_out=40))

    if not summaries:
        return text[:500]

    joined = " ".join(summaries)
    joined = _deduplicate_sentences(joined)

    # If there were multiple chunks, do a final pass to make it coherent
    if len(summaries) > 1 and len(joined) > 200:
        try:
            final = _summarize_text(joined, max_out=200, min_out=60)
            return _deduplicate_sentences(final)
        except Exception:
            pass

    return joined


def _try_groq(text: str) -> str:
    """Summarize using Groq llama-3.1-8b-instant. Returns summary string."""
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY not set in environment / .env file.")

    client  = Groq(api_key=api_key)
    trimmed = text[:MAX_INPUT_CHARS]

    prompt = (
        "You are a professional document summarizer. "
        "Read the following document content and write a concise and meaningful summary in 5-10 sentences. "
        "Focus on the key points, main arguments, and important details.\n\n"
        f"DOCUMENT:\n{trimmed}\n\n"
        "SUMMARY:"
    )

    response = client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=512,
    )
    return response.choices[0].message.content.strip()


class SummarizationEngine:
    """
    Tries local distilBART first; auto-falls back to Groq on any failure.
    Returns dict: {"summary": str, "model_used": str, "error": Optional[str]}
    """

    def summarize(self, text: str) -> dict:
        if not text or not text.strip():
            return {
                "summary":    "No text content found in document.",
                "model_used": "none",
                "error":      None,
            }

        # ── Primary: local BART ───────────────────────────────────────────────
        local_error: Optional[str] = None
        try:
            logger.info("Attempting local summarization...")
            summary = _try_local(text)
            logger.info("Local summarization succeeded.")
            return {
                "summary":    summary,
                "model_used": f"Local ({LOCAL_MODEL_NAME})",
                "error":      None,
            }
        except Exception as exc:
            local_error = str(exc)
            logger.warning(f"Local model failed: {exc}. Falling back to Groq...")

        # ── Fallback: Groq ────────────────────────────────────────────────────
        try:
            logger.info("Attempting Groq summarization...")
            summary = _try_groq(text)
            logger.info("Groq summarization succeeded.")
            return {
                "summary":    summary,
                "model_used": f"Groq ({GROQ_MODEL_NAME})",
                "error":      f"Local model failed: {local_error}",
            }
        except Exception as exc:
            groq_error = str(exc)
            logger.error(f"Groq fallback also failed: {exc}")
            return {
                "summary":    "Summarization failed. Both local model and Groq API returned errors.",
                "model_used": "none",
                "error":      f"Local: {local_error} | Groq: {groq_error}",
            }
