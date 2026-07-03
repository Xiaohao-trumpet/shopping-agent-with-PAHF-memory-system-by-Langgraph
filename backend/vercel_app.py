"""Vercel Python entrypoint.

Vercel's Python service currently flattens the configured service directory into
``/var/task`` and imports this file as a top-level module. The application code
uses package-relative imports, so we register the flattened directory as a
synthetic ``backend`` package before importing ``backend.main``.
"""

from pathlib import Path
import sys
import types


package = types.ModuleType("backend")
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules.setdefault("backend", package)

from backend.main import app
