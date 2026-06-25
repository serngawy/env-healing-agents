"""
Package bootstrap for pytest.

The project directory is named 'env-healing-agents' (hyphens are not valid
Python identifiers), so we register it in sys.modules as 'env_healing_agent'
before any test file is imported. This lets all relative imports inside the
package (from ..core.base_agent, etc.) resolve correctly during test runs.
"""

import importlib.util
import os
import sys
import types

_PKG_NAME = "env_healing_agent"
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_PKG_DIR)

if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

if _PKG_NAME not in sys.modules:
    pkg = types.ModuleType(_PKG_NAME)
    pkg.__path__ = [_PKG_DIR]
    pkg.__package__ = _PKG_NAME
    pkg.__file__ = os.path.join(_PKG_DIR, "__init__.py")
    sys.modules[_PKG_NAME] = pkg
