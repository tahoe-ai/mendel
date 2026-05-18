from __future__ import annotations

import asyncio
import json
import logging
import sys

from .core.orchestrator import scan_all_targets


def main() -> int:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = sys.argv[1:]
    if not args or args[0] != "scan-alerts":
        print("usage: python -m patch_bot scan-alerts", file=sys.stderr)
        return 2
    result = asyncio.run(scan_all_targets())
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
