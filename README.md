# mr-robot-crunch

<img width="3000" height="1240" alt="mr-robot-crunch-lockup-red" src="https://github.com/user-attachments/assets/ec3e416b-78e7-4f6f-b1c4-4d79722b5e1c" />


> Combine words into every possible mutation — a parallel, resumable wordlist generator.

Give it a few words like `text alice carol` and it generates every combination/mutation of
them: word orderings, separators, case variants, leetspeak, and appended digits/years. Output
is streamed to disk, so memory stays bounded even when the wordlist is enormous.

```
  author : rebazomar
  github : https://github.com/rebazomar121/mr-robot-crunch
```

## Quick install

```sh
pipx install git+https://github.com/rebazomar121/mr-robot-crunch.git
```

Then run `mr-robot-crunch`. (More options under [Install](#install).)

## Features

- **Full mutation engine** — subsets + orderings → separators → case → leetspeak → prefixes/suffixes.
- **Tunable exhaustiveness** — each dimension has levels, plus an **EXHAUSTIVE preset** that
  finds *all* possibilities:
  - separators: `single` (one sep everywhere) · `each` (different sep per gap) · `none`
  - case: `whole` · `word` (per-word) · `char` (per-character, 2^letters) · `none`
  - leetspeak: `bounded` · `full` (every letter, multiple targets `a→4/@`) · `none`
  - **prefixes** and **suffixes** (digits, years, symbols) — independently toggleable
  - **digit-run insertion** — insert 0–N digits *between words and at the ends*
    (e.g. `10cat12Bob2024`) without listing tokens
- **Mask / template mode** — give a pattern and the engine fills it, hashcat-style:
  `?d?d{word}?d?d{Word}2024` → `10cat12Bob2024` (`?d/?l/?u/?s/?a` charsets;
  `{word}/{Word}/{WORD}/{w}` word slots; everything else literal).
- **Wizard + menu UI** — pick each level; see an estimated output size before you start.
- **Parallel** — 1–1024 worker processes (default 16) that partition the work so **no two
  workers ever produce the same work**.
- **Single deduplicated output file** in your current directory, named with the words + a
  timestamp (e.g. `mr-robot-crunch_text-alice-carol_20260601-203000.txt`).
- **Resource-aware** — every 5s it checks CPU/memory; if either exceeds 95% it pauses and asks
  whether to continue or stop.
- **Pause anytime** with the `P` key; **stop & resume later** — stopped runs are saved under
  `~/.mr-robot-crunch/` and can be resumed exactly where they left off.
- **Output policy filters** — keep only candidates that match a password policy:
  `--min-len` / `--max-len` and `--require-digit/-symbol/-upper/-lower`. Filtering happens
  *before* anything is written, so it saves the disk it would otherwise waste.
- **Personal-info tokens** (CUPP-style) — `--keyboard-walks` (qwerty, asdf, 1qaz2wsx…) and
  `--date 1997-08-15` (derives `1997`, `97`, `08`, `15`, `1508`, `0815`, `15081997`, …),
  injected as affixes.
- **Pipe straight into a cracker** — `--stdout` streams candidates with bounded memory:
  `mr-robot-crunch run --words "…" --stdout | hashcat -m 0 hash.txt`.
- **Rule-file export** — `--rules-out base.txt:out.rule` emits a small base wordlist plus a
  hashcat/John rule file instead of materialising every candidate (how large attacks are
  really run). Validated against `hashcat --stdout`.
- **gzip output** (`--gzip`) and **`--no-dedup`** (plain concat, fastest) when you don't need
  global dedup.
- **macOS + Linux**, installable with `pipx`. `psutil` is the only dependency, and it's
  optional — without it the resource auto-pause is simply disabled.

## Install

Install straight from GitHub — no checkout needed:

```sh
pipx install git+https://github.com/rebazomar121/mr-robot-crunch.git
```

Or from a local copy:

```sh
pipx install .
# or, for development (editable):
pip install -e .
```

Upgrade / uninstall later:

```sh
pipx upgrade mr-robot-crunch
pipx uninstall mr-robot-crunch
```

## Usage

```sh
mr-robot-crunch            # launch the interactive banner + menu + wizard
mr-robot-crunch list       # list resumable sessions
mr-robot-crunch resume     # pick a saved session to resume
mr-robot-crunch resume <session_id>
```

### Non-interactive `run` (scriptable)

Everything the wizard does is also available as flags, for automation and piping:

```sh
# stream straight into hashcat, bounded memory, no temp files
mr-robot-crunch run --words "alice carol 1997" --leet full --stdout | hashcat -m 0 hash.txt

# write a policy-filtered file in parallel
mr-robot-crunch run --words "alice carol" --exhaustive \
    --min-len 8 --max-len 16 --require-digit --require-symbol \
    --workers 32 -o wordlist.txt -y

# CUPP-style profiling tokens
mr-robot-crunch run --words "cat bob" --keyboard-walks --date 1997-08-15 --stdout

# emit a base wordlist + hashcat rules instead of expanding everything
mr-robot-crunch run --words "alice carol" --case whole --leet full \
    --rules-out base.txt:attack.rule
hashcat -a 0 -m 0 hash.txt base.txt -r attack.rule
```

Key flags: `--words/--stdin/--mask` (input) · `--sep/--case/--leet/--prefix/--no-suffix/--digits`
or `--exhaustive` (layers) · `--min-len/--max-len/--require-*` (filters) ·
`--keyboard-walks/--date` (personal) · `--stdout/-o/--gzip/--no-dedup/--workers/--rules-out`
(output). Run `mr-robot-crunch run -h` for the full list.

> Rule export is approximate: word separators are baked into the base wordlist, and per-word /
> per-character casing collapses to whole-string case rules (a note is printed). Everything
> else — whole-string case, leetspeak, prefixes and suffixes — maps to real `sXY`/`^x`/`$x`
> rules.

### Example: crack a hash in seconds (with hashcat)

A full end-to-end demo you can run yourself. We make a hash from a password we
already know (`alice2024`), then let the tool + [hashcat](https://hashcat.net)
recover it — proving the workflow.

```sh
# 1. Create a target hash from a known password (this stands in for a captured hash)
printf '%s' "alice2024" | md5sum | cut -d' ' -f1 > hash.txt   # Linux
# macOS:  printf '%s' "alice2024" | md5 | awk '{print $NF}' > hash.txt

# 2. Generate guesses with the tool and pipe them straight into hashcat
mr-robot-crunch run --words "alice" --case whole --leet none --stdout \
  | hashcat -m 0 -a 0 hash.txt
```

Output (cracked instantly):

```
28db548ac69921eca66afe7de34f67f5:alice2024
Status...........: Cracked
```

What happened: the tool turned the word `alice` into candidates like `alice`,
`Alice`, `ALICE`, `alice2024`, `Alice!`… and piped them into hashcat, which
hashed each guess and found the one matching the target — `alice2024`.

`-m 0` is the hash type (`0` = MD5); change it to match your target (e.g. `1000`
for NTLM, `1800` for sha512crypt). Run `hashcat --help | grep -i <type>` to find it.

> ⚠️ **Authorized use only.** Password cracking is for systems you own or have
> explicit written permission to test (pentests, CTFs, your own accounts). Using
> it on anyone else's data is illegal.

### Figuring out the hash type

hashcat needs the right `-m` mode or it can't crack. To identify an unknown hash:

**Automated tools (easiest):**

```sh
pip install hashid name-that-hash

hashid -m '28db548ac69921eca66afe7de34f67f5'   # -m shows the hashcat mode
nth   -t '28db548ac69921eca66afe7de34f67f5'    # name-that-hash: type + -m + John format
```

**Recognize it by shape** (length / prefix):

| Looks like | Type | hashcat `-m` |
|---|---|---|
| 32 hex chars (`28db548a…`) | MD5 | `0` |
| 32 hex chars (no prefix) | NTLM (Windows) | `1000` |
| 40 hex chars | SHA-1 | `100` |
| 64 hex chars | SHA-256 | `1400` |
| 128 hex chars | SHA-512 | `1700` |
| starts `$2a$/$2b$/$2y$` | bcrypt | `3200` |
| starts `$1$` | md5crypt | `500` |
| starts `$6$` | sha512crypt | `1800` |

**Browse hashcat's own reference:**

```sh
hashcat --help | grep -i sha256      # find the mode for a known type
hashcat --example-hashes | less      # a sample of every supported hash format
```

> Note: you can identify the *likely* format, but not always with certainty — MD5
> and NTLM are both "32 hex chars" and look identical, so tools list several
> candidates and you may need to try a couple of `-m` values.
>
> `RS256`/`HS256` are JWT **signing** algorithms, not password hashes — different
> tooling (e.g. `hashcat -m 16500` for JWT), not the table above.

### Controls during a run

- `P` — pause and choose **[C]ontinue** or **[S]top & save**.
- An automatic pause prompt appears if CPU or memory usage exceeds 95% (checked every 5s).
- When there's no TTY (piped or cron runs), there's no key to read a decision from, so an
  auto-pause is logged and the run continues instead of hanging.

## How combinations work

For input words `bob alice @` in **exhaustive** mode, the engine produces things like:

```
bob@alice
t3xt.4lic3
BOB@ALICE
alicE            (per-character case)
BOB@alice        (per-word case)
bob@alice_carol  (different separator per gap)
23bob@alicE      (prefix + per-char case)
carol2024        (suffix)
...
```

In the lighter (non-exhaustive) levels these explosive variants are skipped, so the wordlist
stays manageable. Pick the level per dimension in the wizard, or choose the EXHAUSTIVE preset.

When the run finishes, each per-worker shard is sorted in parallel and then k-way merged with
`sort -m -u` (memory-bounded, disk-backed) so the final file contains **no duplicate lines**,
even across worker processes. If `sort` is unavailable, it falls back to an in-memory dedup;
`--no-dedup` skips the merge entirely.

## Notes

- Output can become extremely large; the wizard warns you and shows an estimate first
  (and flags it in red once the estimate passes ~1e9 candidates).
- Requires an interactive terminal for the `P` pause key (macOS/Linux).
- Pure Python — the only runtime dependency is `psutil` (optional).
