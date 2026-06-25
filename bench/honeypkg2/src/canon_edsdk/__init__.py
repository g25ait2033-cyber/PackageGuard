"""canon-edsdk — research honey-package (TestPyPI only). Harmless, does nothing.

This name is an LLM *hallucination* (slopsquatting): models routinely invent a
Python `canon-edsdk` package to drive a Canon DSLR, but no such package exists on
real PyPI — the only real "Canon EDSDK" is Canon's C/C++ SDK, so this is also a
cross-language confusion (cf. the paper's Table 3). It is published to TestPyPI
ONLY for the PackageGuard study and is not a real library. Like honeypkg #1 it
also advertises a GitHub repo, to model an attacker that backs the squat with a
freshly-created repository. See README.
"""

__version__ = "0.0.1"

_NOTICE = (
    "canon-edsdk is a research honey-package (TestPyPI only) for the PackageGuard "
    "slopsquatting study. It is not a real library and does nothing. "
    "Run `pip uninstall canon-edsdk`."
)

# Print once on import so anyone who installs it understands what it is.
print(_NOTICE)
