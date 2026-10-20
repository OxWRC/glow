"""Console-script entry point for glow-api.

`glow_api.settings` builds `Settings()` at import time and most modules import
it, so bad GLOW_* env fails before click runs. Catch that here and print
readable lines instead of a traceback.
"""

import sys

from pydantic import ValidationError


def format_validation_error(exc: ValidationError) -> list[str]:
    lines = []
    for err in exc.errors():
        name = "GLOW_" + ".".join(str(p) for p in err["loc"])
        lines.append(f"{name}: {err['msg']}")
    return lines


def main() -> None:
    try:
        from glow_api.cli import cli
    except ValidationError as exc:
        for line in format_validation_error(exc):
            print(line, file=sys.stderr)
        sys.exit(1)
    cli()
