## 2026-07-24 - Python Regex vs Native String Splitting for Whitespace Normalization
**Learning:** Using `re.sub(r"\s+", " ", t).strip()` for collapsing whitespace in Python string normalization is twice as slow as Python's native `' '.join(t.split())`. Additionally, pre-compiling regex alone without replacing the `re.sub` call for whitespace offered no speedup.
**Action:** Prefer `' '.join(text.split())` over `re.sub(r"\s+", ...)` when normalizing whitespace in hot string-processing paths.
