"""Pytest configuration and global fixtures."""

from __future__ import annotations

import ast
import warnings

import pytest

# Ignore LibreSSL runtime warning from urllib3 on older macOS Python builds
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

try:
    import ssl  # noqa: F401
except (ImportError, ModuleNotFoundError):
    import sys
    from unittest.mock import MagicMock
    mock_ssl = MagicMock()
    mock_ssl.OPENSSL_VERSION_INFO = (3, 0, 0, 0, 0)
    mock_ssl.OPENSSL_VERSION = "OpenSSL 3.0.0"
    mock_ssl.create_default_context.return_value = MagicMock()
    sys.modules["ssl"] = mock_ssl
    sys.modules["_ssl"] = mock_ssl

try:
    import pandas  # noqa: F401
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _get_top_level_imports(filepath: str) -> set[str]:
    """Parse top-level imports of a test module without executing it."""
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
        needed: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for n in node.names:
                    needed.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                needed.add(node.module.split(".")[0])
        return needed
    except Exception:
        return set()


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path, config) -> bool:
    """Ignore test files requiring pandas or numpy when running in a pure-Python environment."""
    str_path = str(collection_path)
    if not str_path.endswith(".py") or "conftest.py" in str_path:
        return False

    if not HAS_PANDAS or not HAS_NUMPY:
        needed = _get_top_level_imports(str_path)
        if not HAS_PANDAS and "pandas" in needed:
            return True
        if not HAS_NUMPY and "numpy" in needed:
            return True
    return False
