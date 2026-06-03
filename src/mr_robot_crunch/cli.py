"""Command-line interface: banner, menu, wizard, run loop, resume."""

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import banner, controls, engine, output, partition, rules, session
from .monitor import ResourceMonitor
from .worker import run_worker

DEFAULT_WORKERS = 16
MIN_WORKERS = 1
MAX_WORKERS = 1024

# Each tunable dimension: prompt + ordered (value, label) choices + default.
SEP_CHOICES = [
    ("single", "one separator across all gaps (_  -  .  @  123)"),
    ("each", "every separator at every gap (bob@alice_carol) — exhaustive"),
    ("none", "no separators (just concatenate)"),
]
CASE_CHOICES = [
    ("whole", "whole-string forms (lower / UPPER / Capitalize / ...)"),
    ("word", "per-word casing (BOB@alice, bob@Alice ...)"),
    ("char", "per-character casing (alicE, alIce ...) — 2^letters, exhaustive"),
    ("none", "keep original case"),
]
LEET_CHOICES = [
    ("bounded", "bounded leet (original / all-substituted / one-class)"),
    ("full", "full leet (every letter, multiple targets a->4/@ ...) — exhaustive"),
    ("none", "no leetspeak"),
]


# --------------------------------------------------------------------------
# small input helpers (cooked-mode, used outside the run loop)
# --------------------------------------------------------------------------

def _ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            ans = input(prompt + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print(banner.dim("  please answer y or n"))


def _ask_choice(prompt: str, choices, default_value):
    """Ask the user to pick one of ``choices`` (list of (value, label))."""
    print(banner.dim("  " + prompt))
    default_idx = 1
    for i, (value, label) in enumerate(choices, 1):
        mark = ""
        if value == default_value:
            default_idx = i
            mark = banner.dim(" (default)")
        print(f"     {i}) {label}{mark}")
    pick = _ask_int("   choose", default_idx, 1, len(choices))
    return choices[pick - 1][0]


def _ask_int(prompt: str, default: int, lo: int, hi: int) -> int:
    while True:
        try:
            raw = input(f"{prompt} [{default}] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not raw:
            return default
        try:
            val = int(raw)
        except ValueError:
            print(banner.dim(f"  enter a whole number between {lo} and {hi}"))
            continue
        if val < lo:
            val = lo
        elif val > hi:
            val = hi
        return val


# --------------------------------------------------------------------------
# wizard
# --------------------------------------------------------------------------

def _ask_mask_mode(words: List[str]) -> Optional[Dict]:
    """Offer mask/template mode. Returns a cfg dict, or None for layered mode."""
    print(banner.dim(
        "\n  Mask/template mode lets you place tokens anywhere, e.g.\n"
        "    ?d?d{word}?d?d{Word}2024   ->  10cat12Bob2024\n"
        "    ?d = digit  ?l = lower  ?u = upper  ?s = symbol  ?a = any\n"
        "    {word}=lower  {Word}=Capitalized  {WORD}=UPPER  {w}=any case"
    ))
    if not _ask_yes_no("  Use mask/template mode?", default=False):
        return None
    while True:
        try:
            mask = input("  Enter mask:\n  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not mask:
            print(banner.dim("  empty mask — switching to layered mode"))
            return None
        slots = engine.parse_mask(mask, words)
        if not slots:
            print(banner.red("  mask produced nothing — try again"))
            continue
        cfg = engine.default_config()
        cfg["mask"] = mask
        return cfg


def _ask_filters_and_personal(words: List[str], layers: Dict) -> None:
    """Optional output filters + personal-info tokens, shared by all wizard paths."""
    if _ask_yes_no("\n  Add output filters (length / required characters)?", default=False):
        layers["min_len"] = _ask_int("   minimum length (0 = none)", 0, 0, 256)
        layers["max_len"] = _ask_int("   maximum length (0 = none)", 0, 0, 256)
        layers["req_digit"] = _ask_yes_no("   require at least one digit?", default=False)
        layers["req_symbol"] = _ask_yes_no("   require at least one symbol?", default=False)
        layers["req_upper"] = _ask_yes_no("   require an uppercase letter?", default=False)
        layers["req_lower"] = _ask_yes_no("   require a lowercase letter?", default=False)

    extra: List[str] = []
    if layers.get("mask"):
        return
    if _ask_yes_no("  Add common keyboard walks (qwerty, asdf, 1qaz...)?", default=False):
        extra += engine.keyboard_walks()
    if _ask_yes_no("  Add date fragments from a date (e.g. a birthday)?", default=False):
        try:
            raw = input("   enter date (e.g. 1997-08-15): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raw = ""
        if raw:
            toks = engine.date_tokens(raw)
            if toks:
                print(banner.dim(f"   → {', '.join(toks[:8])}"
                                 + (" ..." if len(toks) > 8 else "")))
                extra += toks
    if extra:
        layers["extra_affixes"] = list(dict.fromkeys(extra))


def run_wizard() -> Optional[Dict]:
    print(banner.cyan("\n  ── New crunch ─────────────────────────────────────"))
    try:
        raw = input("  Enter the words to combine (space separated):\n  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    words = [w for w in raw.split() if w]
    if not words:
        print(banner.red("  No words entered. Aborting."))
        return None
    if len(words) > 8:
        print(
            banner.yellow(
                f"  {len(words)} words is a lot — output may be enormous. Continuing anyway."
            )
        )

    layers = _ask_mask_mode(words)
    if layers is None:
        print(banner.dim("\n  Word orderings & subsets are always generated."))
        if _ask_yes_no(
            "  EXHAUSTIVE mode — find ALL possibilities (every layer at maximum)?",
            default=False,
        ):
            layers = engine.max_config()
            print(banner.yellow("  → exhaustive: per-gap separators, per-character case, "
                                "full leet, prefixes + suffixes"))
        else:
            layers = engine.default_config()
            print()
            layers["sep"] = _ask_choice("Separators between words:", SEP_CHOICES, layers["sep"])
            layers["case"] = _ask_choice("Case variants:", CASE_CHOICES, layers["case"])
            layers["leet"] = _ask_choice("Leetspeak:", LEET_CHOICES, layers["leet"])
            layers["prefix"] = _ask_yes_no(
                "   Prepend digits/years/symbols (prefixes, e.g. 23bob)?", default=False)
            layers["suffix"] = _ask_yes_no(
                "   Append digits/years/symbols (suffixes, e.g. bob2024)?", default=True)
            if _ask_yes_no(
                "   Insert digit-runs between words AND at the ends (e.g. 10cat12Bob)?",
                default=False,
            ):
                layers["digits"] = _ask_int(
                    f"     digit-run length N (1..{engine.MAX_DIGIT_RUN}, inserts 0..N digits)",
                    2, 1, engine.MAX_DIGIT_RUN,
                )

    _ask_filters_and_personal(words, layers)

    estimate = engine.estimate_total(words, layers)
    if estimate >= 10 ** 12:
        shown = f"{estimate:.2e} (~{estimate.bit_length()} bits)"
    else:
        shown = f"{estimate:,}"
    print(banner.cyan(f"\n  Estimated candidates (before dedup): ~{shown}"))
    if estimate >= 10 ** 9:
        print(banner.red("  This is gigantic — it may never finish or fit on disk. "
                         "Consider fewer layers."))

    print()
    print(banner.red("  ╔══════════════════════════════════════════════════════════╗"))
    print(banner.red("  ║  WARNING: this process can consume a HUGE amount of CPU   ║"))
    print(banner.red("  ║  and disk space, and may run for a very long time.        ║"))
    print(banner.red("  ╚══════════════════════════════════════════════════════════╝"))
    if not _ask_yes_no("  Do you want to continue?", default=False):
        print(banner.dim("  Cancelled."))
        return None

    workers = _ask_int(
        f"\n  How many parallel processes? (min {MIN_WORKERS}, max {MAX_WORKERS})",
        DEFAULT_WORKERS,
        MIN_WORKERS,
        MAX_WORKERS,
    )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    sid = session.new_session_id(words, stamp)
    out_name = f"mr-robot-crunch_{session._slug(words)}_{stamp}.txt"
    output_path = str(Path.cwd() / out_name)

    return {
        "session_id": sid,
        "words": words,
        "layers": layers,
        "num_workers": workers,
        "output_path": output_path,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running",
    }


# --------------------------------------------------------------------------
# run controller
# --------------------------------------------------------------------------

class Runner:
    def __init__(self, state: Dict, resume: bool = False):
        self.state = state
        self.resume = resume
        self.sid = state["session_id"]
        self.words = state["words"]
        self.layers = state["layers"]
        self.num_workers = state["num_workers"]
        self.output_path = Path(state["output_path"])
        self.dedup = state.get("dedup", True)
        self.gzip_out = state.get("gzip", False)

        self.pause_event = mp.Event()
        self.stop_event = mp.Event()
        self.counter = mp.Value("Q", 0)
        self.procs: List[mp.Process] = []
        self.monitor = ResourceMonitor(self.pause_event, self.stop_event)

    def _spawn(self) -> None:
        # Cap workers at the number of work units (no point spawning idle workers).
        total_bases = engine.count_bases(self.words, self.layers)
        n = max(1, min(self.num_workers, total_bases))
        self.num_workers = n
        self.state["num_workers"] = n

        for wid in range(n):
            skip = session.read_cursor(self.sid, wid) if self.resume else 0
            p = mp.Process(
                target=run_worker,
                args=(
                    self.sid,
                    wid,
                    n,
                    self.words,
                    self.layers,
                    skip,
                    self.pause_event,
                    self.stop_event,
                    self.counter,
                ),
                daemon=False,
            )
            p.start()
            self.procs.append(p)

    def _alive(self) -> bool:
        return any(p.is_alive() for p in self.procs)

    def _progress_line(self, start_ts: float) -> str:
        elapsed = int(time.monotonic() - start_ts)
        cpu, mem = self.monitor.snapshot()
        with self.counter.get_lock():
            written = self.counter.value
        return (
            f"\r  {banner.green('●')} workers:{self.num_workers}  "
            f"written:{written:,}  "
            f"cpu:{cpu:4.0f}%  mem:{mem:4.0f}%  "
            f"t:{elapsed}s   {banner.dim('[P]=pause')}   "
        )

    def _handle_pause(self, reason: str) -> bool:
        """Prompt continue/stop. Returns True to continue, False to stop."""
        if not controls.interactive():
            # No keyboard to read a decision from (piped/cron run): can't ask,
            # so log the condition and keep going rather than hang forever.
            print(banner.yellow(f"\n  ⏸  {reason} — no TTY to prompt; continuing."))
            self.monitor.auto_pause_reason = None
            self.pause_event.clear()
            return True
        print()  # leave the progress line in place
        print(banner.yellow(f"\n  ⏸  Paused — {reason}"))
        print("     [C] continue    [S] stop & save (resume later)")
        while True:
            ch = controls.read_key_blocking()
            if ch in ("c", "C"):
                self.monitor.auto_pause_reason = None
                self.pause_event.clear()
                print(banner.green("  ▶  resuming...\n"))
                return True
            if ch in ("s", "S"):
                self.stop_event.set()
                self.pause_event.clear()
                print(banner.yellow("  ■  stopping and saving session...\n"))
                return False

    def run(self) -> int:
        session.save_state(self.state)
        self.monitor.start()
        self._spawn()

        start_ts = time.monotonic()
        stopped = False
        is_tty = controls.interactive()

        with controls.cbreak_terminal():
            while self._alive():
                key = controls.read_key(0.3)
                if not is_tty:
                    # No keyboard to poll; avoid busy-spinning the progress loop.
                    time.sleep(0.5)
                if key in ("p", "P") and not self.pause_event.is_set():
                    self.pause_event.set()
                    if not self._handle_pause("you pressed P"):
                        stopped = True
                        break
                elif self.pause_event.is_set():
                    reason = self.monitor.auto_pause_reason or "system threshold exceeded"
                    if not self._handle_pause(reason):
                        stopped = True
                        break
                sys.stdout.write(self._progress_line(start_ts))
                sys.stdout.flush()

        # Wait for children to settle.
        for p in self.procs:
            p.join()

        print()
        if stopped or self.stop_event.is_set():
            self.state["status"] = "stopped"
            session.save_state(self.state)
            self._report_stopped()
            return 0

        return self._finalize()

    def _report_stopped(self) -> None:
        print(banner.yellow("\n  Session saved. Resume it later with:"))
        print(banner.cyan(f"    mr-robot-crunch resume {self.sid}"))
        print(banner.dim(f"  (state under {session.session_dir(self.sid)})"))

    def _finalize(self) -> int:
        self.stop_event.set()  # tell the monitor thread to exit
        if self.dedup:
            print(banner.dim("  Merging shards and removing duplicates..."))
        else:
            print(banner.dim("  Merging shards (no dedup)..."))
        shards = [session.shard_path(self.sid, w) for w in range(self.num_workers)]
        count, method = output.merge_shards(
            shards, self.output_path, dedup=self.dedup, gzip_out=self.gzip_out
        )
        session.cleanup_session(self.sid)
        noun = "unique words" if self.dedup else "lines"
        print(banner.green(f"\n  ✔ Done. {count:,} {noun} written via {method}."))
        print(banner.cyan(f"    {self.output_path}"))
        return 0


# --------------------------------------------------------------------------
# menu + resume
# --------------------------------------------------------------------------

def _resume_picker() -> Optional[Dict]:
    sessions = session.list_sessions()
    if not sessions:
        print(banner.dim("\n  No saved sessions to resume."))
        return None
    print(banner.cyan("\n  Saved sessions:"))
    for i, s in enumerate(sessions, 1):
        print(
            f"   {i}) {s['session_id']}  "
            + banner.dim(f"words={' '.join(s['words'])}  saved={s.get('updated_at','?')}")
        )
    choice = _ask_int("  Pick a session (0 to cancel)", 0, 0, len(sessions))
    if choice == 0:
        return None
    return sessions[choice - 1]


def resume_session(state: Dict) -> int:
    state["status"] = "running"
    print(banner.cyan(f"\n  Resuming {state['session_id']} ..."))
    return Runner(state, resume=True).run()


def _about() -> None:
    print(banner.render_banner())
    print(banner.dim("  Pure-Python (only psutil) wordlist mutation engine."))
    print(banner.dim("  Combines words via orderings, separators, case, leet & suffixes."))


def menu_loop() -> int:
    banner.print_banner()
    while True:
        print(banner.cyan("\n  Menu:"))
        print("   1) New crunch")
        print("   2) Resume session")
        print("   3) About")
        print("   4) Exit")
        try:
            choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "1":
            cfg = run_wizard()
            if cfg:
                Runner(cfg).run()
        elif choice == "2":
            st = _resume_picker()
            if st:
                resume_session(st)
        elif choice == "3":
            _about()
        elif choice in ("4", "q", "exit", "quit"):
            print(banner.dim("  bye."))
            return 0
        else:
            print(banner.dim("  unknown choice"))


# --------------------------------------------------------------------------
# non-interactive `run`
# --------------------------------------------------------------------------

def _collect_words(args) -> List[str]:
    if args.stdin:
        raw = sys.stdin.read()
    else:
        raw = args.words or ""
    return [w for w in raw.replace(",", " ").split() if w]


def _cfg_from_args(args) -> Dict:
    if args.exhaustive:
        cfg = engine.max_config()
    else:
        cfg = engine.default_config()
        cfg.update({
            "sep": args.sep, "case": args.case, "leet": args.leet,
            "prefix": args.prefix, "suffix": args.suffix, "digits": args.digits,
        })
    if args.mask:
        cfg["mask"] = args.mask
    # output policy filters
    cfg.update({
        "min_len": args.min_len, "max_len": args.max_len,
        "req_digit": args.require_digit, "req_symbol": args.require_symbol,
        "req_upper": args.require_upper, "req_lower": args.require_lower,
    })
    # personal-info tokens -> extra affixes
    extra: List[str] = []
    if args.keyboard_walks:
        extra += engine.keyboard_walks()
    for d in (args.date or []):
        extra += engine.date_tokens(d)
    cfg["extra_affixes"] = list(dict.fromkeys(extra))
    return cfg


def _stream_stdout(words: List[str], cfg: Dict, dedup: bool) -> int:
    """Generate straight to stdout (for piping into a cracker). No multiprocessing."""
    keep = engine.make_predicate(cfg)
    seen = set() if dedup else None
    write = sys.stdout.write
    count = 0
    try:
        for base in engine.iter_bases(words, cfg):
            for word in engine.expand_base(base, words, cfg):
                if keep is not None and not keep(word):
                    continue
                if seen is not None:
                    if word in seen:
                        continue
                    seen.add(word)
                write(word)
                write("\n")
                count += 1
    except BrokenPipeError:
        pass  # downstream (e.g. `head`/hashcat) closed the pipe; that's fine
    return count


def cmd_run(args) -> int:
    words = _collect_words(args)
    if not words and not args.mask:
        print(banner.red("  No words given. Use --words \"a b c\", --stdin, or --mask."),
              file=sys.stderr)
        return 1
    cfg = _cfg_from_args(args)

    # Rule-file export: emit base wordlist + hashcat rules and stop.
    if args.rules_out:
        if ":" not in args.rules_out:
            print(banner.red("  --rules-out must be WORDS_PATH:RULES_PATH"), file=sys.stderr)
            return 1
        wpath, rpath = args.rules_out.split(":", 1)
        n_words, n_rules = rules.export(words, cfg, Path(wpath), Path(rpath))
        print(banner.green(f"  ✔ {n_words:,} base words -> {wpath}"), file=sys.stderr)
        print(banner.green(f"  ✔ {n_rules:,} rules      -> {rpath}"), file=sys.stderr)
        print(banner.dim(f"  Try: hashcat -a 0 -m 0 hashes.txt {wpath} -r {rpath}"),
              file=sys.stderr)
        return 0

    # Stream straight to stdout (no files, no merge).
    if args.stdout:
        _stream_stdout(words, cfg, dedup=(not args.no_dedup))
        return 0

    # File output via the parallel Runner.
    estimate = engine.estimate_total(words, cfg)
    if not args.yes:
        print(banner.cyan(f"  Estimated candidates (before dedup/filter): ~{estimate:,}"),
              file=sys.stderr)
        if estimate >= 10 ** 9:
            print(banner.red("  This is gigantic; pass -y to proceed or narrow the layers."),
                  file=sys.stderr)
            return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    sid = session.new_session_id(words or ["mask"], stamp)
    if args.output:
        output_path = str(Path(args.output).expanduser().resolve())
    else:
        suffix = ".txt.gz" if args.gzip else ".txt"
        out_name = f"mr-robot-crunch_{session._slug(words or ['mask'])}_{stamp}{suffix}"
        output_path = str(Path.cwd() / out_name)

    state = {
        "session_id": sid, "words": words, "layers": cfg,
        "num_workers": max(MIN_WORKERS, min(args.workers, MAX_WORKERS)),
        "output_path": output_path, "dedup": not args.no_dedup, "gzip": args.gzip,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "running",
    }
    return Runner(state).run()


def _add_run_parser(sub) -> None:
    p = sub.add_parser("run", help="generate non-interactively (scriptable)")
    src = p.add_argument_group("input")
    src.add_argument("--words", help="words to combine (space- or comma-separated)")
    src.add_argument("--stdin", action="store_true", help="read words from stdin")
    src.add_argument("--mask", help="mask/template, e.g. '?d?d{word}2024'")

    lay = p.add_argument_group("layers")
    lay.add_argument("--sep", choices=["none", "single", "each"], default="single")
    lay.add_argument("--case", choices=["none", "whole", "word", "char"], default="whole")
    lay.add_argument("--leet", choices=["none", "bounded", "full"], default="bounded")
    lay.add_argument("--prefix", action="store_true", help="prepend digits/years/symbols")
    lay.add_argument("--suffix", dest="suffix", action="store_true",
                     help="append digits/years/symbols (default on)")
    lay.add_argument("--no-suffix", dest="suffix", action="store_false")
    p.set_defaults(suffix=True)
    lay.add_argument("--digits", type=int, default=0,
                     metavar="N", help=f"insert digit-runs of length 1..N (max {engine.MAX_DIGIT_RUN})")
    lay.add_argument("--exhaustive", action="store_true",
                     help="every layer at maximum (overrides the layer flags)")

    pol = p.add_argument_group("output policy filters")
    pol.add_argument("--min-len", type=int, default=0, metavar="N")
    pol.add_argument("--max-len", type=int, default=0, metavar="N")
    pol.add_argument("--require-digit", action="store_true")
    pol.add_argument("--require-symbol", action="store_true")
    pol.add_argument("--require-upper", action="store_true")
    pol.add_argument("--require-lower", action="store_true")

    per = p.add_argument_group("personal info")
    per.add_argument("--keyboard-walks", action="store_true",
                     help="add common keyboard walks (qwerty, asdf, 1qaz...) as affixes")
    per.add_argument("--date", action="append", metavar="DATE",
                     help="derive date fragments (e.g. 1997-08-15); repeatable")

    out = p.add_argument_group("output")
    out.add_argument("-o", "--output", help="output file path")
    out.add_argument("--stdout", action="store_true", help="stream to stdout (pipe-friendly)")
    out.add_argument("--gzip", action="store_true", help="gzip the output file")
    out.add_argument("--no-dedup", action="store_true", help="skip global dedup (faster)")
    out.add_argument("--workers", type=int, default=DEFAULT_WORKERS, metavar="N")
    out.add_argument("--rules-out", metavar="WORDS:RULES",
                     help="emit base wordlist + hashcat rule file instead of expanding")
    out.add_argument("-y", "--yes", action="store_true", help="skip the size confirmation")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mr-robot-crunch",
        description="Combine words into every possible mutation (wordlist generator).",
    )
    sub = parser.add_subparsers(dest="command")

    _add_run_parser(sub)

    p_resume = sub.add_parser("resume", help="resume a saved session")
    p_resume.add_argument("session_id", nargs="?", help="session id (omit to pick from a list)")

    sub.add_parser("list", help="list resumable sessions")

    args = parser.parse_args(argv)

    if args.command == "run":
        return cmd_run(args)

    if args.command == "list":
        for s in session.list_sessions():
            print(f"{s['session_id']}\t{' '.join(s['words'])}\t{s.get('updated_at','?')}")
        return 0

    if args.command == "resume":
        if args.session_id:
            st = session.load_state(args.session_id)
            if not st:
                print(banner.red(f"  No such session: {args.session_id}"))
                return 1
        else:
            banner.print_banner()
            st = _resume_picker()
            if not st:
                return 0
        return resume_session(st)

    return menu_loop()


if __name__ == "__main__":
    sys.exit(main())
