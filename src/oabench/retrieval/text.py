"""French-friendly text normalisation shared by the lexical engines.

Deliberately simple: lower-case, accents removed, a short stop-word list and
plural folding. Whether a real stemmer helps is a question for the evaluation,
not something to assume here.
"""

from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a au aux avec ce ces cette dans de des du d elle en et est il ils je l la le les leur
    lui ma mais me mes mon ne nos notre nous on ou où par pas pour qu que qui sa se ses son
    sur ta te tes ton tu un une vos votre vous y the of and for in on to
    """.split()  # noqa: SIM905 (a word list reads better as prose)
)


def fold(text: str) -> str:
    """Lower-case and strip accents: "Écoles" -> "ecoles"."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _singular(token: str) -> str:
    if len(token) > 3 and token[-1] in "sx" and not token.isdigit():
        return token[:-1]
    return token


_FOLDED_STOPWORDS = frozenset(fold(word) for word in STOPWORDS)


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(fold(text))
    return [_singular(t) for t in tokens if t not in _FOLDED_STOPWORDS and len(t) > 1]
