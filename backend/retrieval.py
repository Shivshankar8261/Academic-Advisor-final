"""
Retrieval: top-k semantic search (with a BM25 keyword boost) over the chunk index (backend/index, built by ingest.py) + exact course lookup.

The index is ~440 chunks, so exact cosine search with numpy (one matrix-vector product) is as fast as an ANN
store and needs no database - which keeps the deployed backend small.

Pure embedding search is weak at matching course codes such as "DATA301" (MiniLM has no idea what
that token means), so any course code or exact course title mentioned in the query is also looked
up directly in the catalogue and its record is added to the results (a small "hybrid" step).
"""
import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np

import embedder
from config import INDEX_DIR, PROCESSED_DIR, TOP_K

CODE_RE = re.compile(r"\b([A-Za-z]{3,4})\s?(\d{3})\b")
MAX_PER_SECTION = 2
POLICY_DOCS = {"Student Handbook Aug 2026.pdf", "SOP_STUDENT_17082026.pdf"}
COVERAGE_MIN_SCORE = 0.5
# Keyword (BM25) boost added to the cosine score for RANKING only. Embeddings miss rare exact words such as a title
# ("Dean"), a name or "LL.B."; the boost lifts chunks that contain them. The returned score stays the cosine, so the
# MIN_RELEVANCE calibration is unchanged.
LEXICAL_WEIGHT = 0.15
LEXICAL_MIN_COSINE = 0.15
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for", "is", "are", "be", "do", "does",
             "what", "which", "who", "how", "many", "much", "can", "i", "my", "me", "there", "any", "with", "about",
             "tell", "vu", "university", "vidyashilp", "s"}


@lru_cache
def records() -> list[dict]:
    """Every chunk as {text, source_file, section, processed_file, course_code, pages}, in index order."""
    return json.loads((INDEX_DIR / "chunks.json").read_text())


@lru_cache
def _embeddings() -> np.ndarray:
    return np.load(INDEX_DIR / "embeddings.npy")


@lru_cache
def _bm25() -> dict[str, list[tuple[int, float]]]:
    """term -> [(chunk index, BM25 weight)], k1 = 1.5, b = 0.75."""
    docs = [TOKEN_RE.findall(r["text"].lower()) for r in records()]
    n, avg = len(docs), sum(map(len, docs)) / max(len(docs), 1)
    df = Counter(t for d in docs for t in set(d))
    postings = defaultdict(list)
    for i, d in enumerate(docs):
        norm = 1.5 * (0.25 + 0.75 * len(d) / avg)
        for t, f in Counter(d).items():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            postings[t].append((i, idf * f * 2.5 / (f + norm)))
    return postings


def lexical_scores(query: str) -> np.ndarray:
    """BM25 scores scaled to 0-1 (best chunk = 1)."""
    s = np.zeros(len(records()), dtype=np.float32)
    for t in set(TOKEN_RE.findall(query.lower())) - STOPWORDS:
        for i, w in _bm25().get(t, ()):
            s[i] += w
    return s / s.max() if s.max() > 0 else s


def _where(**match) -> list[dict]:
    """Chunks whose metadata equals every given value (a value may be a set of allowed values)."""
    return [dict(r) for r in records()
            if all(r.get(k) in v if isinstance(v, (set, frozenset)) else r.get(k) == v for k, v in match.items())]


@lru_cache
def _title_index() -> dict[str, str]:
    """lower-case course title -> course code, for exact-title matching."""
    cat = json.loads((PROCESSED_DIR / "course_catalogue.json").read_text())
    return {re.sub(r"\s+", " ", c["title"].lower()).strip(): code for code, c in cat.items() if len(c["title"]) > 6}


def mentioned_courses(text: str) -> list[str]:
    codes = {f"{a.upper()}{b}" for a, b in CODE_RE.findall(text)}
    low = text.lower()
    codes |= {code for title, code in _title_index().items() if title in low}
    return sorted(codes)


def retrieve(query: str, k: int = TOP_K) -> list[dict]:
    """Return chunks as dicts: {text, source_file, section, score}."""
    scores = _embeddings() @ embedder.encode([query])[0]  # cosine similarity (all vectors are normalised)
    # Over-fetch, then keep at most MAX_PER_SECTION chunks per section so one long section (e.g. the
    # malpractice rules for "examination" questions) cannot crowd every other document out of the top k.
    lexical = lexical_scores(query)
    top = np.argsort(-(scores + LEXICAL_WEIGHT * lexical))[:k * 4]
    pool = [{**records()[i], "score": round(float(scores[i]), 3)} for i in top]
    hits, per_section = [], {}
    for h in pool:
        base = re.sub(r", pp?\. [\d-]+$", "", h["section"])  # same section on another page = same section
        if per_section.get(base, 0) >= MAX_PER_SECTION:
            continue
        per_section[base] = per_section.get(base, 0) + 1
        hits.append(h)
        if len(hits) == k:
            break
    # Cross-document coverage: rules about the same topic often appear in BOTH the Handbook and the SOP (and
    # sometimes disagree). If one of them has a clearly relevant chunk but was crowded out, add its best one.
    have = {h["source_file"] for h in hits}
    for doc in POLICY_DOCS - have:
        best = next((h for h in pool if h["source_file"] == doc and h["score"] >= COVERAGE_MIN_SCORE), None)
        if best:
            hits.append(best)

    # Keyword coverage: a list of names (a faculty page) embeds poorly, so the best keyword match is always offered
    # too, as long as it is not unrelated to the question.
    best = int(np.argmax(lexical))
    if lexical[best] > 0 and scores[best] >= LEXICAL_MIN_COSINE and \
            records()[best]["section"] not in {h["section"] for h in hits}:
        hits.append({**records()[best], "score": round(float(scores[best]), 3)})

    # exact course lookups (hybrid step)
    seen = {h["section"] for h in hits}
    for code in mentioned_courses(query):
        for m in _where(course_code=code):
            if m["section"] not in seen:
                hits.append({**m, "score": 1.0})
                seen.add(m["section"])
    return hits


def prerequisite_records(chunks: list[dict]) -> list[dict]:
    """One-hop expansion: add catalogue records for the prerequisites of courses already retrieved.
    Used by rag_structured so eligibility checks can see the whole prerequisite chain."""
    have = {c["section"] for c in chunks}
    codes = set()
    for c in chunks:
        if c.get("course_code"):
            for line in c["text"].splitlines():
                if "prerequisites:" in line:
                    codes |= {f"{a.upper()}{b}" for a, b in CODE_RE.findall(line.split("prerequisites:")[1])}
    extra = []
    for code in sorted(codes):
        for m in _where(course_code=code):
            if m["section"] not in have:
                extra.append({**m, "score": 1.0})
                have.add(m["section"])
    return extra


@lru_cache
def _all_sections() -> tuple[str, ...]:
    return tuple(sorted({m["section"] for m in records()}))


def section_records(prefix: str) -> list[dict]:
    """All chunks of one document section, e.g. 'Section III - Academic Regulations > 12. Progression'."""
    names = [s for s in _all_sections() if s.startswith(prefix)]
    if not names:
        return []
    return [{**m, "score": 1.0} for m in _where(section=frozenset(names))]


@lru_cache
def _structure_sections() -> list[str]:
    return sorted({m["section"] for m in _where(processed_file="programme_structure.md")})


def semester_records(batch: str, semesters: list[int]) -> list[dict]:
    """Structured lookup of the student's own batch/semester course lists from the semester spread."""
    prefixes = tuple(f"{batch} Batch - Semester {s} " for s in semesters)  # sections end with "(tab ...)"
    names = [sec for sec in _structure_sections() if sec.startswith(prefixes)]
    if not names:
        return []
    return [{**m, "score": 1.0} for m in _where(section=frozenset(names))]
