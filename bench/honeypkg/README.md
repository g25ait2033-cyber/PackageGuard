# pybloomberg — research honey-package (DO NOT USE)

> **This is not a real package.** It is a harmless research artifact published to
> **TestPyPI only** as part of an academic study of LLM *package hallucinations*
> (a.k.a. *slopsquatting*).

`pybloomberg` is one of the package names that large language models **invent**
when asked to write code — it does not, and should not, exist as a real library.
We publish a deliberately empty, harmless version of it to **TestPyPI**
(`https://test.pypi.org`, an isolated sandbox index that is **not** the real
Python Package Index) to demonstrate the following point:

> A freshly-published package **genuinely exists** on a live index, yet a good
> trust engine should still **refuse to trust it**, because existence alone is a
> terrible signal of safety. Account age, download history, a real source
> repository, real-world usage, and an install-time code scan all matter more.

## This package does nothing

- No `setup.py` install hooks.
- No post-install scripts.
- On import it only prints a one-line research notice.

If you installed this by accident, simply `pip uninstall pybloomberg`. It cannot
harm you — but the fact that a name an LLM hallucinated could be installed at all
is exactly the supply-chain risk our project studies and defends against.

## Ethics

The original research (Spracklen et al., *“We Have a Package for You!”*, USENIX
Security 2025) deliberately did **not** publish hallucinated names to the real
PyPI, to avoid poisoning the ecosystem. We follow the same discipline: this
artifact lives on **TestPyPI only** and is removed after the project
demonstration.
