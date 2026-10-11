"""Free-form model text is qualitative; deterministic fields own the figures."""
import re


def qualitative_text_error(texts):
    """Conservative literal-number policy, not a semantic fraud/quantity classifier."""
    if any("<think" in text.lower() or "</think" in text.lower() for text in texts):
        return "LLM_INVALID_OUTPUT"
    if any(re.search(r"\d|[%\uff05]", text) for text in texts):
        return "LLM_UNSUPPORTED_EVIDENCE"
    return None
