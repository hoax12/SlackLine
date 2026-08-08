"""ALL model calls live here and nowhere else.

selector.py and narrator.py each expose a keyless deterministic path so the
pipeline completes with zero keys configured; providers.py holds the
Gemini-primary / Groq-fallback interface and token accounting.
"""
