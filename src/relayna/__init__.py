"""relayna package root.

Import concrete functionality from submodules such as ``relayna.rabbitmq``
or ``relayna.status_store`` rather than from the package root.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("relayna")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
