from __future__ import annotations

import argparse
import logging
import sys

from .orchestrator import Chimera


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chimera",
        description="Adaptive, selector-free Android UI automation.")
    parser.add_argument("command", nargs="+",
                        help="Natural-language instruction, e.g. 'Open WhatsApp'")
    parser.add_argument("--db", default="chimera.db",
                        help="Path to selector cache DB")
    parser.add_argument("--serial", default=None,
                        help="ADB device serial (defaults to the only connected device)")
    parser.add_argument("--appium-url", default=None,
                        help="Appium server URL (default: $APPIUM_URL or "
                             "http://127.0.0.1:4723)")
    parser.add_argument("--ollama-url", default=None)
    parser.add_argument("--ollama-model", default=None)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    level = logging.WARNING - (10 * args.verbose)
    logging.basicConfig(level=max(level, logging.DEBUG),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    nl = " ".join(args.command).strip()
    if not nl:
        parser.error("empty command")

    ch = Chimera(db_path=args.db, serial=args.serial,
                 appium_url=args.appium_url,
                 ollama_url=args.ollama_url, ollama_model=args.ollama_model)
    try:
        ch.run(nl)
    finally:
        ch.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
