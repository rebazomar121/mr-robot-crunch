"""Streaming word-mutation engine.

Two generation modes share one streaming, generator-based core so memory stays
bounded no matter how large the wordlist becomes:

LAYERED mode (the default) runs each base word-permutation through a pipeline:

    base tuple
      -> per-word case      (optional; cases each word independently)
      -> join w/ separators (single separator, per-gap product, or none;
                             digit-runs can be added to the separator set)
      -> whole/per-char case
      -> leetspeak          (none / bounded / full)
      -> prefix + suffix     (affixes and/or digit-runs at the ends)

MASK mode enumerates a user template such as ``?d?d{word}?d?d{Word}2024`` —
``?d/?l/?u/?s/?a`` are character classes and ``{word}/{Word}/{WORD}/{w}`` are
input-word slots. Everything else is literal.

Both modes expose the same three hooks so the worker/partition code is uniform:

    iter_bases(words, cfg)   -> deterministic stream of "work units"
    count_bases(words, cfg)  -> how many work units there are
    expand_base(base, words, cfg) -> stream of result strings for one unit

Each work unit's expansion is duplicate-free by construction, so workers stream
straight to disk; cross-unit / cross-worker duplicates are removed once,
globally, by the final ``sort -u`` merge.

Config keys:
    sep    : "none" | "single" | "each"
    case   : "none" | "whole"  | "word" | "char"
    leet   : "none" | "bounded"| "full"
    prefix : bool
    suffix : bool
    digits : int   (0 = off; otherwise insert digit-runs of length 1..N
                    between words and at the ends)
    mask   : str   (if set, MASK mode is used and the layered keys are ignored)
"""

import re
import string
from itertools import permutations, product
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

# --- configuration tables --------------------------------------------------

SEPARATORS: List[str] = ["", "_", "-", ".", "@", "123"]

_DIGITS = [str(n) for n in range(10)]
_TWO_DIGITS = [f"{n:02d}" for n in range(100)]
_YEARS = [str(y) for y in range(1990, 2031)]
_SYMBOLS = ["!", "?", "@", "#", "$", "123", "!@#", "007"]
AFFIXES: List[str] = list(dict.fromkeys(_DIGITS + _TWO_DIGITS + _YEARS + _SYMBOLS))

LEET_SIMPLE: Dict[str, str] = {
    "a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7",
}
LEET_FULL: Dict[str, List[str]] = {
    "a": ["4", "@"], "b": ["8"], "e": ["3"], "g": ["9", "6"], "i": ["1", "!"],
    "l": ["1"], "o": ["0"], "s": ["5", "$"], "t": ["7", "+"], "z": ["2"],
}

MAX_DIGIT_RUN = 4  # cap N so digit-run sets stay finite

# Characters that count as "symbols" for the --require-symbol policy filter.
SYMBOL_CHARS = frozenset("!@#$%^&*()-_=+[]{};:,.<>?/|~`'\"\\")

CONFIG_KEYS = (
    "sep", "case", "leet", "prefix", "suffix", "digits", "mask",
    # output policy filters
    "min_len", "max_len", "req_digit", "req_symbol", "req_upper", "req_lower",
    # personal-info tokens injected into the affix pools
    "extra_affixes",
)


def _filter_defaults() -> Dict:
    return {"min_len": 0, "max_len": 0, "req_digit": False, "req_symbol": False,
            "req_upper": False, "req_lower": False, "extra_affixes": []}


def default_config() -> Dict:
    cfg = {"sep": "single", "case": "whole", "leet": "bounded",
           "prefix": False, "suffix": True, "digits": 0, "mask": ""}
    cfg.update(_filter_defaults())
    return cfg


def max_config() -> Dict:
    cfg = {"sep": "each", "case": "char", "leet": "full",
           "prefix": True, "suffix": True, "digits": 0, "mask": ""}
    cfg.update(_filter_defaults())
    return cfg


# --- output policy filtering -----------------------------------------------


def make_predicate(cfg: Dict) -> Optional[Callable[[str], bool]]:
    """Build a keep/drop predicate from the policy keys, or None if no filter.

    Returning None lets callers skip the per-line call entirely on the common
    (unfiltered) path.
    """
    min_len = int(cfg.get("min_len", 0) or 0)
    max_len = int(cfg.get("max_len", 0) or 0)
    req_d = bool(cfg.get("req_digit", False))
    req_s = bool(cfg.get("req_symbol", False))
    req_u = bool(cfg.get("req_upper", False))
    req_l = bool(cfg.get("req_lower", False))
    if not (min_len or max_len or req_d or req_s or req_u or req_l):
        return None

    symbols = SYMBOL_CHARS

    def keep(s: str) -> bool:
        n = len(s)
        if min_len and n < min_len:
            return False
        if max_len and n > max_len:
            return False
        if req_d and not any(c.isdigit() for c in s):
            return False
        if req_u and not any(c.isupper() for c in s):
            return False
        if req_l and not any(c.islower() for c in s):
            return False
        if req_s and not any(c in symbols for c in s):
            return False
        return True

    return keep


# --- personal-info tokens (keyboard walks, dates) --------------------------

_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")
_KEYBOARD_EXTRAS = (
    "qwerty", "qwertyuiop", "asdf", "asdfgh", "zxcvbn", "qazwsx",
    "1qaz2wsx", "zaq12wsx", "qweasdzxc", "1q2w3e4r", "qweasd",
)


def keyboard_walks(min_len: int = 4) -> List[str]:
    """Common keyboard-walk strings (left-to-right runs of each row + classics)."""
    walks: List[str] = []
    seen = set()

    def add(s: str) -> None:
        if len(s) >= min_len and s not in seen:
            seen.add(s)
            walks.append(s)

    for row in _KEYBOARD_ROWS:
        for length in range(min_len, len(row) + 1):
            for i in range(len(row) - length + 1):
                seg = row[i:i + length]
                add(seg)
                add(seg[::-1])
    for extra in _KEYBOARD_EXTRAS:
        add(extra)
    return walks


def date_tokens(text: str) -> List[str]:
    """Derive common password date fragments from a date string.

    Accepts anything containing a year and (optionally) month/day in either
    order, separated by ``-``, ``/``, ``.`` or nothing — e.g. ``1997-08-15``,
    ``15/08/1997``, ``19970815``. Produces YYYY, YY, MM, DD and the usual
    concatenations people actually use in passwords.
    """
    nums = re.findall(r"\d+", text)
    year = month = day = None
    for part in nums:
        if len(part) == 4 and 1900 <= int(part) <= 2099 and year is None:
            year = part
    twos = [p for p in nums if len(p) <= 2]
    # First 1-2 digit value that looks like a day/month pair.
    if len(twos) >= 2:
        a, b = int(twos[0]), int(twos[1])
        # Prefer day-first if the first value can't be a month.
        if a > 12 and b <= 12:
            day, month = f"{a:02d}", f"{b:02d}"
        elif b > 12 and a <= 12:
            month, day = f"{a:02d}", f"{b:02d}"
        else:
            day, month = f"{a:02d}", f"{b:02d}"
    elif len(twos) == 1 and year is None:
        # A lone 1-2 digit number is ambiguous; skip month/day.
        pass

    out: List[str] = []
    seen = set()

    def add(s: str) -> None:
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    if year:
        add(year)
        add(year[2:])  # YY
    if month:
        add(month)
    if day:
        add(day)
    if day and month:
        add(day + month)
        add(month + day)
        if year:
            yy = year[2:]
            add(day + month + yy)
            add(day + month + year)
            add(month + day + year)
            add(year + month + day)
    return out


# --- helpers ---------------------------------------------------------------


def _digit_runs(n: int) -> List[str]:
    """All fixed-width digit strings of length 1..n (e.g. n=2 -> '0'..'9','00'..'99')."""
    n = max(0, min(int(n), MAX_DIGIT_RUN))
    runs: List[str] = []
    for length in range(1, n + 1):
        for num in range(10 ** length):
            runs.append(str(num).zfill(length))
    return runs


def _word_case_forms(word: str) -> List[str]:
    out, seen = [], set()
    for f in (word.lower(), word.upper(), word.capitalize(), word.swapcase(), word):
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# --- base enumeration (layered mode) ---------------------------------------


def base_permutations(words: List[str]) -> Iterator[Tuple[str, ...]]:
    n = len(words)
    for k in range(1, n + 1):
        for perm in permutations(words, k):
            yield perm


def count_base_permutations(n: int) -> int:
    total, term = 0, 1
    for k in range(1, n + 1):
        term *= (n - k + 1)
        total += term
    return total


# --- layered pipeline stages -----------------------------------------------


def _apply_word_case(tuples: Iterable[Tuple[str, ...]], mode: str) -> Iterator[Tuple[str, ...]]:
    for t in tuples:
        if mode != "word":
            yield t
            continue
        for combo in product(*(_word_case_forms(w) for w in t)):
            yield combo


def _separator_set(cfg: Dict) -> Tuple[List[str], bool]:
    sep = cfg.get("sep", "single")
    base = [""] if sep == "none" else list(SEPARATORS)
    if cfg.get("digits", 0):
        base = list(dict.fromkeys(base + _digit_runs(cfg["digits"])))
    return base, (sep == "each")


def _affix_pool(cfg: Dict, enabled: bool, include_extra: bool = False) -> List[str]:
    pool = [""]
    if enabled:
        pool += AFFIXES
    if cfg.get("digits", 0):  # digit-runs go at the ends too
        pool += _digit_runs(cfg["digits"])
    if include_extra and cfg.get("extra_affixes"):
        pool += list(cfg["extra_affixes"])
    return list(dict.fromkeys(pool))


def _join(tuples: Iterable[Tuple[str, ...]], sep_set: List[str], each: bool) -> Iterator[str]:
    for t in tuples:
        if len(t) == 1:
            yield t[0]
            continue
        if each:
            for combo in product(sep_set, repeat=len(t) - 1):
                parts = [t[0]]
                for i, w in enumerate(t[1:]):
                    parts.append(combo[i])
                    parts.append(w)
                yield "".join(parts)
        else:
            for sep in sep_set:
                yield sep.join(t)


def _per_char_case(s: str) -> Iterator[str]:
    idx = [i for i, ch in enumerate(s) if ch.isalpha()]
    if not idx:
        yield s
        return
    chars = list(s)
    for mask in range(1 << len(idx)):
        for bit, i in enumerate(idx):
            chars[i] = s[i].upper() if (mask >> bit) & 1 else s[i].lower()
        yield "".join(chars)


def _apply_string_case(strings: Iterable[str], mode: str) -> Iterator[str]:
    for s in strings:
        if mode == "whole":
            seen = set()
            for v in (s.lower(), s.upper(), s.capitalize(), s.title(), s.swapcase(), s):
                if v not in seen:
                    seen.add(v)
                    yield v
        elif mode == "char":
            yield from _per_char_case(s)
        else:
            yield s


def _leet_bounded(s: str) -> Iterator[str]:
    yield s
    lower = s.lower()
    present = [ch for ch in LEET_SIMPLE if ch in lower]
    if not present:
        return
    full = s
    for ch, repl in LEET_SIMPLE.items():
        full = full.replace(ch, repl).replace(ch.upper(), repl)
    if full != s:
        yield full
    for ch in present:
        repl = LEET_SIMPLE[ch]
        single = s.replace(ch, repl).replace(ch.upper(), repl)
        if single != s and single != full:
            yield single


def _leet_full(s: str) -> Iterator[str]:
    options = []
    for ch in s:
        targets = LEET_FULL.get(ch.lower())
        options.append([ch] + targets if targets else [ch])
    for combo in product(*options):
        yield "".join(combo)


def _apply_leet(strings: Iterable[str], mode: str) -> Iterator[str]:
    for s in strings:
        if mode == "full":
            yield from _leet_full(s)
        elif mode == "bounded":
            seen = set()
            for v in _leet_bounded(s):
                if v not in seen:
                    seen.add(v)
                    yield v
        else:
            yield s


def _apply_affixes(strings: Iterable[str], pres: List[str], sufs: List[str]) -> Iterator[str]:
    for s in strings:
        for p in pres:
            for su in sufs:
                yield p + s + su


def mutate(bases: Iterable[Tuple[str, ...]], cfg: Dict) -> Iterator[str]:
    """Run a stream of base tuples through the configured layered pipeline."""
    case = cfg.get("case", "whole")
    sep_set, each = _separator_set(cfg)
    prefix_on = cfg.get("prefix", False)
    # Personal tokens (dates/keyboard walks) are always tried as a suffix, and
    # as a prefix only when prefixes are enabled.
    pres = _affix_pool(cfg, prefix_on, include_extra=prefix_on)
    sufs = _affix_pool(cfg, cfg.get("suffix", True), include_extra=True)
    stream = _apply_word_case(bases, case)
    stream = _join(stream, sep_set, each)
    stream = _apply_string_case(stream, case)
    stream = _apply_leet(stream, cfg.get("leet", "bounded"))
    stream = _apply_affixes(stream, pres, sufs)
    return stream


# --- mask / template mode --------------------------------------------------


def _mask_charsets() -> Dict[str, List[str]]:
    cs = {
        "d": list(string.digits),
        "l": list(string.ascii_lowercase),
        "u": list(string.ascii_uppercase),
        "s": list("!@#$%^&*"),
    }
    cs["a"] = cs["l"] + cs["u"] + cs["d"] + cs["s"]
    return cs


def parse_mask(mask: str, words: List[str]) -> List[List[str]]:
    """Tokenise ``mask`` into a list of option-lists; the full output is the
    cartesian product of the lists joined together."""
    cs = _mask_charsets()
    word_slots = {
        "word": [w.lower() for w in words],
        "Word": [w.capitalize() for w in words],
        "WORD": [w.upper() for w in words],
        "w": [],
    }
    seen = set()
    for w in words:
        for v in _word_case_forms(w):
            if v not in seen:
                seen.add(v)
                word_slots["w"].append(v)

    slots: List[List[str]] = []
    lit: List[str] = []

    def flush():
        if lit:
            slots.append(["".join(lit)])
            lit.clear()

    i, n = 0, len(mask)
    while i < n:
        ch = mask[i]
        if ch == "?" and i + 1 < n and mask[i + 1] in cs:
            flush()
            slots.append(list(cs[mask[i + 1]]))
            i += 2
            continue
        if ch == "{":
            j = mask.find("}", i)
            if j != -1 and mask[i + 1:j] in word_slots:
                flush()
                slots.append(list(word_slots[mask[i + 1:j]]))
                i = j + 1
                continue
        lit.append(ch)
        i += 1
    flush()
    return slots


def _first_multi_slot(slots: List[List[str]]):
    for i, s in enumerate(slots):
        if len(s) > 1:
            return i
    return None


# --- unified base abstraction (used by partition + worker) -----------------


def iter_bases(words: List[str], cfg: Dict) -> Iterator:
    """Yield deterministic work units for either mode."""
    mask = cfg.get("mask")
    if mask:
        slots = parse_mask(mask, words)
        first = _first_multi_slot(slots)
        if first is None:
            yield ("mask", None)
            return
        for opt in slots[first]:
            yield ("mask", (first, opt))
    else:
        yield from base_permutations(words)


def count_bases(words: List[str], cfg: Dict) -> int:
    mask = cfg.get("mask")
    if mask:
        slots = parse_mask(mask, words)
        first = _first_multi_slot(slots)
        return 1 if first is None else len(slots[first])
    return count_base_permutations(len(words))


def expand_base(base, words: List[str], cfg: Dict) -> Iterator[str]:
    """Expand a single work unit into result strings."""
    mask = cfg.get("mask")
    if mask:
        slots = parse_mask(mask, words)
        _, fix = base
        if fix is not None:
            first, opt = fix
            slots = list(slots)
            slots[first] = [opt]
        for combo in product(*slots):
            yield "".join(combo)
    else:
        yield from mutate([base], cfg)


# --- estimation ------------------------------------------------------------


def estimate_total(words: List[str], cfg: Dict) -> int:
    """Rough upper-bound estimate of output size (before dedup)."""
    if cfg.get("mask"):
        total = 1
        for s in parse_mask(cfg["mask"], words):
            total *= max(1, len(s))
        return total

    n = len(words)
    if n == 0:
        return 0
    avg = max(1, round(sum(len(w) for w in words) / n))
    case = cfg.get("case", "whole")
    leet = cfg.get("leet", "bounded")
    sep_set, each = _separator_set(cfg)
    sep_n = len(sep_set)
    prefix_on = cfg.get("prefix", False)
    pres = len(_affix_pool(cfg, prefix_on, include_extra=prefix_on))
    sufs = len(_affix_pool(cfg, cfg.get("suffix", True), include_extra=True))

    total, term = 0, 1
    for k in range(1, n + 1):
        term *= (n - k + 1)
        per = 1.0
        if case == "word":
            per *= 4 ** k
        if k > 1:
            per *= (sep_n ** (k - 1)) if each else sep_n
        alpha = k * avg
        if case == "whole":
            per *= 6
        elif case == "char":
            per *= 2 ** alpha
        if leet == "bounded":
            per *= (1 + len(LEET_SIMPLE))
        elif leet == "full":
            per *= 2 ** alpha
        per *= pres * sufs
        total += int(term * per)
    return total
