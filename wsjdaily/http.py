"""Shared HTTP helper.

Every network call in this project goes through curl. The invocation is
load-bearing: -L follows Google's consent redirect (without it the resolver
gets an empty 302 body), and the browser User-Agent avoids bot walls.
Lives here rather than in generate.py so source adapters can use it without
importing generate, which would create a circular import.
"""
import subprocess

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def curl(args: list[str]) -> str:
    """Run curl with the project's standard flags and return stdout."""
    return subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A", UA] + args,
        capture_output=True, text=True,
    ).stdout
