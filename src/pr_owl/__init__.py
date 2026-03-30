"""pr-owl: Audit outbound GitHub PRs for mergeability and guide remediation."""

try:
    from pr_owl._version import __version__
except ImportError:
    __version__ = "0.0.0.dev0"
