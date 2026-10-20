"""Entry point for ``python -m agentvc``. Prints the version until the CLI exists."""

from agentvc import __version__


def main() -> int:
    print(f"agentvc {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
