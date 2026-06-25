# PackageGuard

> CSL6010 Major Project · IIT Jodhpur
> Based on *We Have a Package for You!* (Spracklen et al., USENIX Security 2025, Distinguished Paper)

PackageGuard is a **post-generation defense against LLM package hallucinations**
("slopsquatting"). Code-generating LLMs routinely invent package names that do not
exist (e.g. `pip install fastapi-utilz` when only `fastapi-utils` is real). An
attacker can register those invented names and ship malware to anyone who copy-pastes
the command. The paper measures this threat; PackageGuard is a working defense.

It guards at two points, both backed by one **trust engine** that scores any package
name `0–100` from live, multi-source evidence:

| Tier | Component | Where it sits | What it stops |
|---|---|---|---|
| **Tier 1** | `packageguard/` proxy | Between the LLM and the user | The user ever *seeing* a hallucinated name — the answer is inspected and the model is asked to correct itself |
| **Tier 2** | `safeinstall/` CLI | At `pip install` time | The user installing *any* untrusted package, LLM-suggested or not |

Unlike a static allow-list (which the paper shows an attacker defeats by simply
publishing the hallucinated name), PackageGuard re-evaluates trust **live** from 8
independent signals, so a freshly-published squat with no history is still flagged.

> **Demo video & source paper:** see [ASSETS.md](ASSETS.md) (the ~154 MB walkthrough
> video is hosted on Google Drive, not in this repo).

---

## Table of contents

1. [Folder structure](#folder-structure)
2. [The trust engine — the 8 collectors](#the-trust-engine--the-8-collectors)
3. [Scoring and verdicts](#scoring-and-verdicts)
4. [Setup](#setup)
5. [Running the demos](#running-the-demos)
6. [Configuration reference](#configuration-reference)
7. [Testing](#testing)
8. [Reproducing the benchmarks](#reproducing-the-benchmarks)
9. [Future work](#future-work)
10. [Ethics](#ethics)

---

## Folder structure

```
.
├── trust_engine/              # THE BRAIN — scores a package name from live evidence
│   ├── collectors/            #   the 8 signal collectors (one file each)
│   │   ├── nameanalysis.py    #   typo-squat / name-anomaly heuristic (no network)
│   │   ├── registry.py        #   PyPI existence, age, release count, URLs
│   │   ├── osv.py             #   known vulnerabilities (OSV.dev)
│   │   ├── maintainer.py      #   author / email legitimacy
│   │   ├── repo.py            #   GitHub stars, age, activity
│   │   ├── installscan.py     #   pattern-scan install scripts for malware
│   │   ├── codesearch.py      #   real-world import usage on GitHub
│   │   └── selfcheck.py       #   (opt-in) ask the LLM "is this package real?"
│   ├── data/popular_pypi.json #   reference list for name-distance checks
│   ├── score.py               #   orchestrator: runs collectors → weighted score → verdict
│   ├── cache.py               #   SQLite TTL cache (repeat lookups are instant)
│   ├── http.py                #   shared HTTP client (retries, timeouts, index override)
│   └── types.py               #   Signal + TrustReport dataclasses
│
├── packageguard/              # TIER 1 — LLM proxy middleware
│   ├── proxy.py               #   OpenAI-compatible FastAPI server + live demo logging
│   ├── guard.py               #   pure guard logic (scan answer, annotate, refine prompt)
│   ├── extract.py             #   pull package names out of model output
│   └── llm_client.py          #   thin OpenAI-SDK wrapper (LM Studio / Ollama / cloud)
│
├── safeinstall/               # TIER 2 — trust gate around `pip install`
│   ├── cli.py                 #   `safeinstall check|pip install <pkg>`
│   └── __main__.py            #   enables `python -m safeinstall`
│
├── demo/                      # interactive demos
│   ├── streamlit_app.py       #   WebUI (recommended) — runs the guard in-process
│   ├── chat.py                #   terminal REPL through the running proxy
│   ├── proxy_demo.py          #   one-shot client → Markdown transcript
│   └── transcripts/           #   saved demo transcripts
│
├── bench/                     # measurement harness + slopsquat honey-package experiment
│   ├── elicit.py              #   elicit real hallucinations from local models
│   ├── validate.py            #   accuracy vs. live-PyPI ground truth
│   ├── ablation.py            #   "why 8 collectors if existence is 100%?" study
│   ├── honeypkg_demo.py       #   prove the engine BLOCKs a published squat
│   ├── honeypkg/              #   honey package #1: pybloomberg (TestPyPI only)
│   ├── honeypkg2/             #   honey package #2: canon-edsdk, a "clean stub" (TestPyPI only)
│   └── results/               #   CSVs, JSON reports, plots
│
├── requirements.txt
├── .env.example               # copy to .env and fill in
└── README.md
```

The dependency direction is strictly one-way: both tiers import `trust_engine`;
`trust_engine` imports nothing from the tiers. Build/understand the brain once, then
the two front-ends are thin.

---

## The trust engine — the 8 collectors

Each collector inspects one independent source of evidence and returns a `Signal`
(`score 0–100`, a `weight`, and human-readable `reasons`). `score.py` combines the
signals into a single weighted score. Heavier weight = stronger evidence of (il)legitimacy.

| Collector | Weight | Network | What it proves | How it scores |
|---|---|---|---|---|
| **registry** | 3.0 | PyPI | Does the package even exist, and is it established? | Non-existent → `0` (decisive). Existing: age, release count, repo/homepage URLs, yanked status. |
| **installscan** | 2.5 | PyPI | Does the install script try to do something malicious? | Downloads the sdist and pattern-scans `setup.py`/etc. for shell exec, base64-decode-exec, env-var exfiltration, network calls at install time. **Abstains** when clean. |
| **osv** | 2.0 → 4.0 | OSV.dev | Are there known, *unpatched* vulnerabilities right now? | Splits advisories into patched vs. open; scores on the open set only and **abstains** when clean. Votes with raised weight (4.0) when real risk exists. |
| **repo** | 2.0 | GitHub | Is there a real, healthy project behind it? | Stars, repo age, recent activity, archived/disabled flags; 404 repo is a strong negative. |
| **codesearch** | 2.0 | GitHub | Does anyone in the world actually import this? | Counts public Python files that `import` the package. Zero usage is a hallmark of a hallucinated name. (Requires `GITHUB_TOKEN`.) |
| **maintainer** | 1.5 | PyPI | Is the author a real, accountable person? | Author/maintainer fields and email domain. Custom org domain scores up; placeholder/reserved domains (`example.com`, …) are penalised. |
| **selfcheck** | 1.5 | LLM | Does the model itself believe the package is real? | Opt-in. Asks the LLM "is `<pkg>` a real package?" (paper RQ3: models detect their own hallucinations >75% of the time). Off by default. |
| **nameanalysis** | 1.0 | none | Does the *name* look like a typo-squat or anomaly? | Levenshtein distance to popular packages, digit ratio, separators, length. **Abstains** on a clean name (a plausible name is the default for a squat, so it earns no trust). |

**Why "abstain" matters.** `osv`, `installscan`, and `nameanalysis` return *no vote*
(`score = None`) when they find nothing. Absence of evidence (no known CVE, no malware,
a plausible name) is the **default state of a brand-new squat**, so rewarding it would
hand free trust to attackers. These collectors only vote when they find real evidence
of risk; otherwise they let the existence/usage/reputation signals decide.

---

## Scoring and verdicts

The final score is a weighted average over only the signals that actually voted
(abstaining or errored collectors are skipped):

$$\text{score} = \frac{\sum_i \text{score}_i \cdot \text{weight}_i}{\sum_i \text{weight}_i}$$

| Score | Verdict | Meaning |
|---|---|---|
| **≥ 70** | `PASS` | Established, well-evidenced package — proceed. |
| **40–69** | `WARN` | Real but thin (new, low usage) — verify before installing. |
| **< 40** | `BLOCK` | Untrustworthy — do not install. |

**Hard rule:** if `registry` reports the package does **not exist**, the verdict is
`BLOCK` regardless of any other signal.

---

## Setup

> Commands below use **Windows `cmd.exe`**. On macOS/Linux, swap `\` for `/`,
> `.venv\Scripts\activate` for `source .venv/bin/activate`, and `set VAR=value`
> for `export VAR=value`.

### 1. Prerequisites

- **Python 3.11+** (`python --version`)
- **Git**
- **A GitHub Personal Access Token** (classic, `public_repo` scope) — optional but
  recommended. Without it, GitHub APIs are limited to 60 req/hr (the `repo` and
  `codesearch` collectors will abstain); with it, 5000 req/hr.
- **A local LLM backend** for the live demos — either:
  - **LM Studio** with its Local Server started (Developer tab → Start Server, port 1234), or
  - **Ollama** (`ollama serve`, port 11434) with a code model pulled, e.g.
    `ollama pull qwen2.5-coder:3b-instruct-q4_K_M` (small models hallucinate readily — ideal for the demo).

### 2. Clone and create a virtual environment

```cmd
git clone <your-repo-url> packageguard
cd packageguard
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```cmd
pip install -r requirements.txt
```

### 4. Configure environment variables

```cmd
copy .env.example .env
```

Then edit `.env` and set at least `GITHUB_TOKEN`. The trust engine and CLI read
these automatically. For a quick CLI-only check you can instead set them inline:

```cmd
set GITHUB_TOKEN=ghp_your_token_here
```

**How to create the GitHub token** (takes a minute, free):

1. Sign in to GitHub → click your avatar (top-right) → **Settings**.
2. Bottom of the left menu → **Developer settings** → **Personal access tokens** →
   **Tokens (classic)**.
3. **Generate new token (classic)**. Give it a note (e.g. `packageguard`) and an expiry.
4. Tick **only** the **`public_repo`** scope (listed under `repo`). PackageGuard just
   *reads* public data — it never writes to your account.
5. Click **Generate token** and copy the `ghp_...` value immediately — GitHub will not
   show it again.
6. Paste it into `.env` as `GITHUB_TOKEN=ghp_...`.

> Never commit `.env` to git, and revoke the token from the same page when you are
> done. Without a token the `repo` and `codesearch` collectors simply abstain — the
> rest of the engine still works.

### 5. Smoke test

```cmd
python -m safeinstall check requests
```

You should see `requests` scored **PASS** with supporting reasons. Try a name that
does not exist to see a `BLOCK`:

```cmd
python -m safeinstall check pyght-not-real-xyz-9999
```

---

## Running the demos

There are four ways to see PackageGuard work, from simplest to most complete.

### A. Tier 2 — the `safeinstall` CLI (no LLM needed)

Evaluate packages without installing:

```cmd
python -m safeinstall check requests flask pyght-not-real-xyz
```

Gate a real install (only installs if the package passes / you approve a warning):

```cmd
python -m safeinstall pip install requests
```

Useful flags: `--json` (machine-readable report), `--yes` (auto-approve WARN),
`--strict` (treat WARN as BLOCK), `--no-cache`. Exit code `2` means a package was
blocked.

### B. Tier 1 — the Streamlit WebUI (recommended)

The richest demo. It runs the **same guard logic in-process** (no separate proxy to
start) and lets you pick the backend, model, and policy from the sidebar, then watch
the full flow: *your prompt → model answer → PackageGuard inspects each package →
(if flagged) model is asked to rethink → corrected answer*.

```cmd
streamlit run demo/streamlit_app.py
```

**What you need to run it.** The WebUI has two modes with different requirements:

- **Direct scan** mode scores any package name (including the TestPyPI honey-packages)
  using only the trust engine — it needs **no LLM at all**. A `GITHUB_TOKEN` is
  recommended for full `repo`/`codesearch` coverage. This is the fastest way for a
  reviewer to see the defense without installing anything extra.
- **Chat** mode (the *prompt → answer → refine* flow) needs a chat model. Choose one
  backend in the sidebar:
  - **Ollama (local, free)** — easiest: install [Ollama](https://ollama.com), run
    `ollama serve`, then `ollama pull qwen2.5-coder:3b-instruct-q4_K_M`. Small models
    hallucinate readily, which makes the demo land.
  - **LM Studio (local)** — start its Local Server (port 1234) with a model loaded.
  - **Cloud (OpenAI-compatible)** — *no local model required*: pick "Cloud" in the
    sidebar and enter a base URL + API key for any OpenAI-compatible provider (OpenAI,
    Groq, OpenRouter, …). Use this if you cannot run a local LLM.

Pick a preset prompt (several are tuned to reliably hallucinate on small models),
choose a policy (**annotate** / **block** / **refine**), and submit.

### C. Tier 1 — the proxy + terminal chat

This runs PackageGuard as a standalone **OpenAI-compatible proxy** that any OpenAI
client can point at. Start the proxy in one terminal:

```cmd
set PACKAGEGUARD_UPSTREAM=ollama
set PACKAGEGUARD_POLICY=refine
uvicorn packageguard.proxy:app --port 8000
```

The proxy prints a live, on-screen log of every stage (prompt → model output → scan →
refine prompt → final answer) — handy for a presentation. In a second terminal, chat
through it:

```cmd
python demo/chat.py --model qwen2.5-coder:3b-instruct-q4_K_M
```

Type a coding question (e.g. *"Recommend a Python library to read AVIF images and show
the pip install"*). Inside the REPL: `/full` shows each stage's full text, `/raw`
shows the guard JSON, `/quit` exits.

### D. Tier 1 — one-shot transcript client

With the proxy running (as in C), generate a clean Markdown transcript of the whole
flow into `demo/transcripts/`:

```cmd
python demo/proxy_demo.py --model qwen2.5-coder:3b-instruct-q4_K_M --preset avif
```

### E. The slopsquat honey-package demo (the key result)

This proves the core claim: PackageGuard blocks a hallucinated name **even after an
attacker publishes it**, which a static allow-list cannot. To stay ethical it points
the engine at **TestPyPI only** (an isolated sandbox index, never the real PyPI) via
`PACKAGEGUARD_PYPI_BASE`. Two harmless honey packages are provided: `bench/honeypkg/`
(`pybloomberg`, a plain squat) and `bench/honeypkg2/` (`canon-edsdk`, a "clean stub"
that passes a naive malware scan but is still a hallucination). After uploading either
(or both) to TestPyPI:

```cmd
python bench/honeypkg_demo.py pybloomberg
python bench/honeypkg_demo.py canon-edsdk
```

It contrasts two worlds for the same name: *registry-only (allow-list)* → PASS vs.
*full engine (8 collectors)* → BLOCK, despite the package genuinely existing on the
index.

---

## Configuration reference

All configuration is via environment variables (or `.env`).

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub PAT; enables the `repo` and `codesearch` collectors at full rate. |
| `LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` | LM Studio server endpoint. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama server endpoint. |
| `PACKAGEGUARD_CACHE` | `.cache/trust_cache.sqlite` | SQLite cache location. |
| `PACKAGEGUARD_UPSTREAM` | `ollama` | Proxy backend: `ollama` \| `lmstudio` \| `cloud`. |
| `PACKAGEGUARD_POLICY` | `annotate` | Guard policy: `annotate` \| `block` \| `refine`. |
| `PACKAGEGUARD_MAX_REFINE` | `2` | Max self-refinement rounds in `refine` policy. |
| `PACKAGEGUARD_PYPI_BASE` | `https://pypi.org` | Index to resolve names against; set to `https://test.pypi.org` for the honey-package demo. |
| `PACKAGEGUARD_LOG` | `1` | Live proxy logging on/off. |

For a `cloud` upstream, also set `PACKAGEGUARD_UPSTREAM_BASE_URL` and
`PACKAGEGUARD_UPSTREAM_API_KEY` (any OpenAI-compatible provider works unchanged).

---

## Testing

If the `tests/` folder is included, run the unit tests (mocked — no network or LLM):

```cmd
pytest -q
```

Integration tests that hit the real network/LLM are opt-in:

```cmd
pytest -m integration -v
```

---

## Reproducing the benchmarks

The `bench/` scripts reproduce the measurements behind the project's claims:

- `elicit.py` — run niche prompts through local models and capture real hallucinated
  package names.
- `validate.py` — score a large set of names and compare verdicts against **live PyPI
  existence** as independent ground truth (no circularity).
- `ablation.py` — show that among packages that *all exist*, an existence-only check is
  flat (all PASS) while the full engine grades them, answering "why 8 collectors?".

Each is resumable and writes CSV/JSON reports plus plots into `bench/results/`.

> Note: the trust engine's scoring was tuned after the last full benchmark run, so
> re-run `validate.py` and `ablation.py` before quoting exact accuracy numbers.

---

## Future work

- **More ecosystems.** The architecture is ecosystem-agnostic; only PyPI is
  implemented. npm and crates.io are natural next collectors (the paper notes
  cross-language confusion is a common hallucination source).
- **Dynamic install sandbox.** `installscan` is static (pattern matching). A sandbox
  collector could run `pip install` inside an ephemeral, network-isolated container
  (`docker run --network none`) and watch for real behavioral indicators — at the cost
  of runtime and the usual undecidability/evasion caveats (Rice's theorem).
- **Streaming proxy.** The proxy is non-streaming. A streaming version could buffer
  output, detect package tokens mid-stream, and inject badges without blocking.
- **Tool-calling integration.** Expose `verify_package(name)` as an LLM tool so capable
  models self-correct *before* emitting a name, instead of being corrected after.
- **Weight tuning / learning.** Collector weights are hand-set; they could be fit on a
  labelled corpus of real vs. hallucinated names.
- **Maintainer-history depth.** `maintainer` uses point-in-time PyPI metadata; richer
  account-age and publish-history signals would strengthen it against "clean stub"
  squats.

---

## Ethics

Following the paper's lead, no honey-package is ever published to the real PyPI. The
slopsquat demonstration uses **TestPyPI only** — an isolated sandbox index — and the
honey package is harmless (it only prints a research notice on import). `installscan`
**never executes** package code; it pattern-matches install scripts statically.