# canon-edsdk — research honey-package (DO NOT USE)

> **This is not a real package.** It is a harmless research artifact published to
> **TestPyPI only** as part of an academic study of LLM *package hallucinations*
> (a.k.a. *slopsquatting*).

`canon-edsdk` is one of the package names that large language models **invent**
when asked to write code (e.g. "control a Canon DSLR from Python") — it does not,
and should not, exist as a real PyPI library. The only real "Canon EDSDK" is
Canon's **C/C++** SDK, so this is also a *cross-language confusion* (the model
borrows a real native SDK name and assumes a Python package exists). We publish a
deliberately empty, harmless version to **TestPyPI** (`https://test.pypi.org`, an
isolated sandbox index that is **not** the real Python Package Index).

## What makes this one different from honeypkg #1

This package **also advertises a GitHub repository** in its metadata. It models a
*more determined* attacker who not only publishes the hallucinated package but
also creates a repo to make the squat look legitimate. The point:

> A **freshly-created** repo (0 stars, days old, no real history) carries almost
> no trust. The repo signal nudges up slightly, but the PackageGuard engine
> still returns **BLOCK/WARN** — because existence plus a brand-new repo is still
> not trust. Account age, downloads, real-world usage, repo maturity, and the
> install-time scan all matter more.

## This package does nothing

- No `setup.py` install hooks.
- No post-install scripts.
- On import it only prints a one-line research notice.

If you installed this by accident, simply `pip uninstall canon-edsdk`.

## Ethics

The original research (Spracklen et al., *“We Have a Package for You!”*, USENIX
Security 2025) deliberately did **not** publish hallucinated names to the real
PyPI, to avoid poisoning the ecosystem. We follow the same discipline: this
artifact lives on **TestPyPI only** and is removed after the project
demonstration. The backing GitHub repo is an empty, clearly-labelled research
repo and is likewise deleted afterwards.
