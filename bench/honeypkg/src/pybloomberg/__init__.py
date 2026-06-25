"""pybloomberg — research honey-package (TestPyPI only). Harmless, does nothing.

This name is an LLM *hallucination* (slopsquatting). It is published to TestPyPI
ONLY for the PackageGuard study and is not a real library. See README.
"""

__version__ = "0.0.1"

_NOTICE = (
    "pybloomberg is a research honey-package (TestPyPI only) for the PackageGuard "
    "slopsquatting study. It is not a real library and does nothing. "
    "Run `pip uninstall pybloomberg`."
)

# Print once on import so anyone who installs it understands what it is.
print(_NOTICE)
