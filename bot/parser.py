"""
bot/parser.py
Message parsing utilities — mirrors the C helpers in stats.mod:
  countwords(), countsmileys(), countquestions(), calcwordstats()
"""

import re
from typing import List, Tuple


URL_RE = re.compile(
    r'https?://[^\s<>"\']+|www\.[^\s<>"\']+',
    re.IGNORECASE
)

# Strip IRC colour/bold/underline control codes
CTRL_RE = re.compile(r'\x02|\x03(?:\d{1,2}(?:,\d{1,2})?)?|\x0f|\x16|\x1d|\x1f|\x1e')


def strip_controls(text: str) -> str:
    return CTRL_RE.sub('', text)


def count_words(text: str) -> int:
    return len(text.split())


def count_letters(text: str) -> int:
    return len(text.replace(' ', ''))


def count_smileys(text: str, smiley_list: List[str]) -> int:
    """Count occurrences of any smiley from the config list."""
    count = 0
    for s in smiley_list:
        count += text.count(s)
    return count


def count_sad(text: str, sad_list: List[str]) -> int:
    """Count occurrences of any sad smiley from the config list."""
    count = 0
    for s in sad_list:
        count += text.count(s)
    return count


def count_questions(text: str) -> int:
    """Count lines that contain at least one question mark."""
    return 1 if '?' in text else 0


def extract_urls(text: str) -> List[str]:
    return URL_RE.findall(text)


def extract_words(text: str, min_length: int = 3) -> List[str]:
    """
    Extract meaningful words for wordstats tracking.
    Lowercases, strips punctuation, filters URLs and short words.
    """
    text = strip_controls(text)
    # Remove URLs before splitting
    text = URL_RE.sub('', text)
    # Split on whitespace, strip surrounding punctuation
    tokens = text.split()
    words = []
    for token in tokens:
        word = token.strip('.,!?;:"\'-()[]{}/@#$%^&*~`<>|\\')
        word_lower = word.lower()
        # Filter: must be at least min_length chars, no digits-only, no IRC commands
        # Pass original casing — DB stores lowercase key, display_word tracks original
        if len(word_lower) >= max(min_length, 1) and not word_lower.isdigit():
            words.append(word)
    return words


def words_per_line(words: int, lines: int) -> float:
    if lines == 0:
        return 0.0
    return round(words / lines, 2)


def count_violent(text: str, violent_words: List[str]) -> int:
    """Count /me lines with violent words. Only meaningful for action lines."""
    text_l = text.lower()
    return sum(1 for w in violent_words if w.lower() in text_l)


def count_foul(text: str, foul_words: List[str]) -> int:
    """Count foul words in text."""
    words = text.lower().split()
    return sum(1 for w in words if w.strip(".,!?;:\"'") in foul_words)


def is_all_caps(text: str, threshold: float = 0.75) -> bool:
    """True if this line is shouted (mostly uppercase).
    Matches pisg behaviour: no minimum word count, just checks the
    uppercase ratio of alphabetic characters (min 4 alpha chars).
    """
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) < 4:
        return False
    upper = sum(1 for c in alpha if c.isupper())
    return (upper / len(alpha)) >= threshold


def find_nick_refs(text: str, known_nicks: List[str]) -> List[str]:
    """Return list of known nicks mentioned in text (case-insensitive)."""
    words = set(w.strip(".,!?;:\"'<>@").lower() for w in text.split())
    return [n for n in known_nicks if n.lower() in words]


def count_specific_smileys(text: str, smiley_list: List[str]) -> dict:
    """Return dict of {smiley: count} for each smiley found."""
    result = {}
    for s in smiley_list:
        c = text.count(s)
        if c:
            result[s] = c
    return result


def parse_message(text: str, smiley_list: List[str], sad_list: List[str] = None,
                   min_word_length: int = 3, violent_words: List[str] = None,
                   foul_words: List[str] = None, known_nicks: List[str] = None) -> dict:
    """
    Full parse of a chat message. Returns a dict with all computed values
    ready to be stored.
    """
    clean = strip_controls(text)
    all_smileys = list(smiley_list) + list(sad_list or [])
    return {
        "words":       count_words(clean),
        "letters":     len(clean),
        "smileys":     count_smileys(clean, smiley_list),
        "sad":         count_sad(clean, sad_list or []),
        "smiley_freq": count_specific_smileys(clean, all_smileys),
        "questions":   count_questions(clean),
        "caps":        is_all_caps(clean),
        "violent":     count_violent(clean, violent_words or []),
        "foul":        count_foul(clean, foul_words or []),
        "nick_refs":   find_nick_refs(clean, known_nicks or []),
        "urls":        extract_urls(clean),
        "word_list":   extract_words(clean, min_word_length),
    }
