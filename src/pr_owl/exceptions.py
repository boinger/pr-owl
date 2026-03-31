"""pr-owl exception hierarchy."""


class PrOwlError(Exception):
    """Base exception for all pr-owl errors."""


class GhNotFoundError(PrOwlError):
    """gh CLI is not installed or not on PATH."""


class GhAuthError(PrOwlError):
    """gh auth status check failed — user is not authenticated."""


class GhCommandError(PrOwlError):
    """gh command exited with non-zero status."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.args_run = args
        self.returncode = returncode
        self.stderr = stderr
        cmd = " ".join(args)
        super().__init__(f"gh command failed (exit {returncode}): {cmd}\n{stderr}")


class GhRateLimitError(PrOwlError):
    """GitHub API rate limit exceeded."""


class PrNotFoundError(PrOwlError):
    """PR or repository no longer exists."""
