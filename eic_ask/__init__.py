"""eic-ask package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eic-ask")
except PackageNotFoundError:  # running from a checkout without install
    __version__ = "0+unknown"
