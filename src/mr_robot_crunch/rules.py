"""Export a base wordlist + a hashcat/John rule file instead of materialising
every candidate.

This is how large attacks are actually run: a small base wordlist is fed to the
cracker, which applies thousands of *rules* on the fly (on GPU for hashcat), so
you never write a trillion-line file to disk.

The mapping is necessarily approximate — a few layered dimensions cannot be
expressed as single-token rules:

  * separators between words   -> baked into the base wordlist (one base entry
                                  per word-ordering × separator), lowercased.
  * case (whole)               -> rules ``:`` (as-is), ``u`` (UPPER),
                                  ``c`` (Capitalise), ``C`` (lower-first), ``t``
                                  (toggle all).
  * case (word / char)         -> approximated by the whole-string case rules;
                                  per-word / per-character casing is not
                                  representable as a portable rule (a note is
                                  printed).
  * leet (bounded / full)      -> substitution rules ``sXY`` (e.g. ``sa4``),
                                  individually and combined.
  * prefixes / suffixes        -> ``^x`` (prepend, reversed) and ``$x`` (append)
                                  rules, one per affix token.

Both files are plain text and load directly:

    hashcat -a 0 -m 0 hashes.txt base.txt -r out.rule
    john --wordlist=base.txt --rules=out.rule hashes.txt   # (John dialect mostly overlaps)
"""

from pathlib import Path
from typing import Dict, List, Tuple

from . import engine


def _base_words(words: List[str], cfg: Dict) -> List[str]:
    """Word-orderings joined with each separator option, lowercased, deduped."""
    sep_set, each = engine._separator_set(cfg)
    out: List[str] = []
    seen = set()
    for base in engine.base_permutations(words):
        for joined in engine._join([base], sep_set, each):
            v = joined.lower()
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _case_rules(mode: str) -> List[str]:
    if mode == "none":
        return [":"]
    # whole / word / char all collapse to the representable whole-string set.
    return [":", "u", "c", "C", "t"]


def _esc(ch: str) -> str:
    # hashcat treats most punctuation literally in $/^ ; spaces would break the
    # space-separated rule grammar, so guard against them defensively.
    return ch if ch != " " else ""


def _leet_rules(mode: str) -> List[str]:
    if mode == "none":
        return [":"]
    if mode == "bounded":
        rules = [":"]
        combined = " ".join(f"s{src}{dst}" for src, dst in engine.LEET_SIMPLE.items())
        rules.append(combined)
        for src, dst in engine.LEET_SIMPLE.items():
            rules.append(f"s{src}{dst}")
        return rules
    # full: every documented target, individually and all-combined.
    rules = [":"]
    combined_parts = []
    for src, dsts in engine.LEET_FULL.items():
        for dst in dsts:
            rules.append(f"s{src}{dst}")
        combined_parts.append(f"s{src}{dsts[0]}")
    rules.append(" ".join(combined_parts))
    return _dedupe(rules)


def _affix_rules(cfg: Dict) -> List[str]:
    """One rule per (optional prefix) × (optional suffix) affix token."""
    prefix_on = cfg.get("prefix", False)
    pres = engine._affix_pool(cfg, prefix_on, include_extra=prefix_on)
    sufs = engine._affix_pool(cfg, cfg.get("suffix", True), include_extra=True)

    def prepend(tok: str) -> str:
        # ^ inserts at the front one char at a time, so reverse to preserve order.
        return "".join(f"^{_esc(c)}" for c in reversed(tok))

    def append(tok: str) -> str:
        return "".join(f"${_esc(c)}" for c in tok)

    rules = []
    for p in pres:
        for s in sufs:
            parts = [x for x in (prepend(p), append(s)) if x]
            rules.append(" ".join(parts) if parts else ":")
    return _dedupe(rules)


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def build_rules(cfg: Dict) -> List[str]:
    """Cartesian product of case × leet × affix rule fragments, deduped."""
    case = _case_rules(cfg.get("case", "whole"))
    leet = _leet_rules(cfg.get("leet", "bounded"))
    affix = _affix_rules(cfg)

    rules: List[str] = []
    for c in case:
        for l in leet:
            for a in affix:
                parts = [p for p in (c, l, a) if p and p != ":"]
                rules.append(" ".join(parts) if parts else ":")
    return _dedupe(rules)


def export(words: List[str], cfg: Dict, words_path: Path, rules_path: Path) -> Tuple[int, int]:
    """Write the base wordlist and rule file. Returns ``(n_words, n_rules)``."""
    base = _base_words(words, cfg)
    rules = build_rules(cfg)

    words_path.parent.mkdir(parents=True, exist_ok=True)
    with open(words_path, "w") as fh:
        fh.write("\n".join(base))
        if base:
            fh.write("\n")
    with open(rules_path, "w") as fh:
        fh.write("\n".join(rules))
        if rules:
            fh.write("\n")
    return len(base), len(rules)
