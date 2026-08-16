"""Future application composition root.

Importing this module must never start processes or load models.
"""


def main() -> None:
    """Explain the current bootstrap state without starting incomplete UI."""
    raise SystemExit("AIOpenStudio bootstrap is not implemented yet; see docs/PLAN.md")


if __name__ == "__main__":
    main()
