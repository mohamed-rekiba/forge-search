"""Query translation: user intent → FTS5 expression.

This module exists because of a bug found smoke-testing against a real
repository, and the bug is worth recording because it is *precisely* the
failure this project was built to eliminate.

Searching for ``require-active-ticket`` — a hyphenated filename, the single
most common query shape in the observed workload — produced::

    sqlite3.OperationalError: no such column: active

FTS5 read the hyphens as column syntax. The index held five genuine matches.
The caller received an empty list, because the error was caught and turned
into "no results".

A valid query. Real matches present. Zero results. No error. That is a
silent wrong answer, self-inflicted, in the tool whose entire justification
is eliminating silent wrong answers.

Two fixes, and both are needed:

1. **Escape by default.** A search string is treated as literal text, not as
   an FTS5 expression. Callers look for identifiers and filenames; they should
   not have to know that ``-``, ``:``, ``*``, ``(`` and ``"`` are operators.
2. **Never silently swallow a query error.** An unparseable query must be
   reported as an error, distinguishable from an honest zero-result. Those two
   outcomes mean opposite things and must never look alike.
"""

from __future__ import annotations

import re

__all__ = ["QueryError", "to_fts5", "RAW_PREFIX"]

#: Opt-in escape hatch for callers who genuinely want FTS5 expression syntax
#: (``NEAR``, boolean operators, column filters). Prefixed rather than a
#: separate parameter so it survives any transport without a schema change.
RAW_PREFIX = "fts5:"

# A "bare word" for our purposes: letters, digits, underscore. Everything else
# is a separator to the tokenizer, so anything containing other characters
# must be phrase-quoted to survive parsing.
#
# Underscore belongs on this list only because `core.index` configures the
# tokenizer with `tokenchars '_'`. Stock unicode61 treats '_' as a separator,
# which silently shredded every dunder and snake_case identifier into its
# fragments. The two settings must stay in agreement: widening this pattern
# without widening the tokenizer produces queries that cannot match what was
# indexed.
_BARE = re.compile(r"^\w+$", re.UNICODE)


class QueryError(ValueError):
    """A query that FTS5 cannot parse.

    Deliberately distinct from an empty result set. Conflating them is the bug
    this module was written to fix.
    """


def _quote_phrase(term: str) -> str:
    """Wrap a term as an FTS5 phrase, escaping embedded quotes.

    FTS5 escapes a double quote by doubling it, the same convention as SQL
    string literals.
    """
    return '"' + term.replace('"', '""') + '"'


def to_fts5(query: str) -> str:
    """Translate a user query into a safe FTS5 MATCH expression.

    Whitespace-separated terms are ANDed, which matches what people expect
    from a search box. Each term is phrase-quoted unless it is a bare word, so
    hyphens, dots, colons, and slashes are searched for literally rather than
    being interpreted.

    Prefixing the whole query with ``fts5:`` passes it through untouched, for
    callers who want real expression syntax.

    >>> to_fts5("require-active-ticket")
    '"require-active-ticket"'
    >>> to_fts5("validate token")
    '"validate" "token"'
    >>> to_fts5("fts5:foo OR bar")
    'foo OR bar'
    """
    query = query.strip()
    if not query:
        raise QueryError("query is empty")

    if query.startswith(RAW_PREFIX):
        raw = query[len(RAW_PREFIX) :].strip()
        if not raw:
            raise QueryError("raw query is empty after the 'fts5:' prefix")
        return raw

    terms = query.split()
    return " ".join(t if _BARE.match(t) else _quote_phrase(t) for t in terms)
