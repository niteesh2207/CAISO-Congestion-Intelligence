from __future__ import annotations

import argparse
import json
import sys

from .engine import GridStudioEngine


def main() -> int:
    parser = argparse.ArgumentParser(prog="pwai")
    parser.add_argument("--mode", choices=["demo","simauto"], default="demo")
    parser.add_argument("--case", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--confirm", action="store_true")

    sub.add_parser("status")

    args = parser.parse_args()
    engine = GridStudioEngine(args.mode)
    engine.start(args.case)

    if args.cmd == "status":
        print(json.dumps(engine.status(), indent=2, default=str))
        return 0

    if args.cmd == "ask":
        result = engine.ask(
            args.question,
            confirm_changes=args.confirm,
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
