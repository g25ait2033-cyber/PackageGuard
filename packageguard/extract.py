"""Pull package names out of LLM-generated code/text.

Three sources, in order of confidence:
  1. `pip install X [Y Z]`            -> very direct
  2. `import X` / `from X import ...` -> module-level; map import->dist name later
  3. Common phrasing in prose: "using the X library", "the X package"

Returns a set of normalised lower-case names. Strips submodule paths
(`numpy.linalg` -> `numpy`). Filters obvious stdlib names.
"""

from __future__ import annotations

import re

# A short list — used to filter out import statements that aren't third-party.
# Far from exhaustive; we just suppress the loudest false positives.
STDLIB_HINTS = {
    "os", "sys", "re", "json", "math", "time", "datetime", "random", "logging",
    "collections", "itertools", "functools", "typing", "subprocess", "pathlib",
    "io", "string", "argparse", "asyncio", "threading", "multiprocessing",
    "socket", "ssl", "hashlib", "base64", "email", "html", "http", "urllib",
    "xml", "csv", "sqlite3", "struct", "copy", "warnings", "abc", "contextlib",
    "dataclasses", "enum", "inspect", "operator", "queue", "shutil", "tempfile",
    "traceback", "types", "uuid", "weakref", "zlib", "gzip", "bz2", "lzma",
    "tarfile", "zipfile", "pickle", "platform", "getpass", "decimal", "fractions",
    "statistics", "secrets", "concurrent", "ipaddress", "selectors",
    "telnetlib", "binascii", "unicodedata", "ctypes", "ftplib", "smtplib",
    "imaplib", "poplib", "wave", "audioop", "sunau", "aifc", "colorsys",
    "bisect", "heapq", "array", "textwrap", "difflib", "pprint", "shlex",
    "glob", "fnmatch", "stat", "filecmp", "tkinter", "turtle", "curses",
    "unittest", "doctest", "pdb", "timeit", "cProfile", "profile", "gc",
    "signal", "errno", "fcntl", "termios", "tty", "pty", "resource", "syslog",
    "mmap", "codecs", "locale", "gettext", "calendar", "zoneinfo", "graphlib",
    "numbers", "cmath", "keyword", "token", "tokenize", "ast", "dis", "marshal",
    "importlib", "pkgutil", "runpy", "site", "sysconfig", "builtins", "ffi",
}

# Pure-English / noise words that slip into `pip install` lines or prose when a
# model writes commentary on the same line. None of these are PyPI packages.
STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "if", "in", "on", "to", "of", "with",
    "using", "use", "via", "this", "that", "these", "those", "is", "are", "be",
    "can", "will", "would", "should", "may", "might", "note", "etc", "then",
    "also", "available", "recommended", "standard", "following", "commands",
    "command", "needed", "need", "optional", "stable", "manually", "appropriate",
    "technically", "though", "improved", "official", "instructions", "weights",
    "methods", "error", "root", "elevated", "facilities", "cleanup", "swapped",
    "manufacturer", "session", "as", "git", "https", "http", "github", "system",
    "based", "your", "you", "it", "its", "from", "into", "by", "at", "any",
    "all", "see", "above", "below", "here", "where", "which", "when", "supported",
    "community", "extra", "gpu", "viewer", "java", "optional:", "note:", "github:",
    "their", "several", "some", "many", "most", "such", "other", "another",
}

# Valid PyPI distribution name: letters/digits/._- , must start & end alnum,
# contain at least one letter, length >= 2.
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _is_plausible_name(name: str) -> bool:
    if len(name) < 2:
        return False
    if name in STOPWORDS:
        return False
    if "://" in name or "/" in name or "\\" in name or name.startswith("git+"):
        return False
    if not _VALID_NAME_RE.match(name):
        return False
    if not any(c.isalpha() for c in name):
        return False
    return True

# Some import names != dist names. Keep this list small and explicit.
IMPORT_TO_DIST = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "magic": "python-magic",
    "Levenshtein": "python-Levenshtein",
    "paho": "paho-mqtt",
    "serial": "pyserial",
    "usb": "pyusb",
    "OpenSSL": "pyOpenSSL",
    "Crypto": "pycryptodome",
    "jwt": "PyJWT",
    "git": "GitPython",
    "google": "google-api-python-client",
    "win32com": "pywin32",
    "win32api": "pywin32",
    "skimage": "scikit-image",
    "fitz": "PyMuPDF",
    "attr": "attrs",
    "zmq": "pyzmq",
    "OpenGL": "PyOpenGL",
}

_PIP_RE = re.compile(r"pip\s+install\s+([^\n`\"'#]+)", re.IGNORECASE)
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.MULTILINE)
_PROSE_RE = re.compile(
    r"\b(?:using|with|the|via|use)\s+(?:the\s+)?`?([A-Za-z_][\w.\-]+)`?\s+(?:library|package|module|sdk)",
    re.IGNORECASE,
)
# Indirect mention: a backtick-wrapped name right after an install/use/recommend
# verb, e.g. "use `blpd`", "try `pdblp`", "via the `huectl` wrapper". High
# precision (backticks ~= code identifier) so a model can't slip a flagged
# package back in by referencing it loosely in prose.
_VERB_BACKTICK_RE = re.compile(
    r"\b(?:pip\s+install|install|use|using|try|run|recommend[s]?|consider|via|with)\s+"
    r"(?:the\s+)?`([A-Za-z0-9][\w.\-]+)`",
    re.IGNORECASE,
)


def extract(text: str) -> set[str]:
    """Return the set of distribution names we'd query the trust engine for."""
    names: set[str] = set()

    # 1. pip install lines (highest confidence — name is exactly a dist name)
    for m in _PIP_RE.finditer(text or ""):
        for tok in m.group(1).split():
            tok = tok.strip().strip(",;()")
            if not tok:
                continue
            if tok.startswith("-"):
                continue  # flag like --upgrade; skip but keep scanning
            # strip version spec, e.g. requests==2.31.0
            base = re.split(r"[=<>~!\[]", tok, maxsplit=1)[0].lower()
            # Real `pip install` lines are all package names. The moment we hit an
            # English/stopword token (e.g. "pip install command to add ..."), the
            # rest of the line is prose — stop scanning this line.
            if base in STOPWORDS or base in {"python", "pip", "command"}:
                break
            if base in STDLIB_HINTS:
                continue  # `pip install sqlite3` etc. — stdlib, not a real dist
            # NOTE: do NOT remap via IMPORT_TO_DIST here. On a `pip install` line
            # the token already *is* a distribution name, so `pip install paho`
            # is a genuine hallucination (real dist is `paho-mqtt`) and must be
            # reported as-is, not silently "corrected".
            if _is_plausible_name(base):
                names.add(base)

    # 2. import statements
    for m in _IMPORT_RE.finditer(text or ""):
        mod = m.group(1).split(".")[0]
        if mod.lower() in STDLIB_HINTS or mod.lower() in {"python", "pip"}:
            continue
        dist = IMPORT_TO_DIST.get(mod, mod)
        if _is_plausible_name(dist.lower()):
            names.add(dist.lower())

    # 3. prose mentions (lowest confidence; still useful for explanatory answers)
    for m in _PROSE_RE.finditer(text or ""):
        cand = m.group(1).split(".")[0]  # drop submodule path
        low = cand.lower()
        if low in STDLIB_HINTS:
            continue
        # Skip obvious non-package words
        if low in {"python", "java", "javascript", "code", "function", "openai", "chatgpt"}:
            continue
        if _is_plausible_name(low):
            names.add(low)

    # 4. indirect backtick mentions after an install/use verb ("use `blpd`").
    #    Require length >= 3 here to avoid grabbing terse code vars like `df`.
    for m in _VERB_BACKTICK_RE.finditer(text or ""):
        cand = m.group(1).split(".")[0]
        low = cand.lower()
        if len(low) < 3 or low in STDLIB_HINTS:
            continue
        if low in {"python", "java", "javascript", "code", "function", "openai", "chatgpt", "pip"}:
            continue
        if _is_plausible_name(low):
            names.add(low)

    return names
