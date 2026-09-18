"""CLI seam for non-Python callers (the TypeScript email classifier).

    echo "prompt" | python3 -m iga_llm --tier cheap
    python3 -m iga_llm --resolve --tier smart      # print provider + model, no call

Prompt on stdin, completion on stdout, errors on stderr with exit 1.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import LLMError, PROVIDERS, TIERS, complete, resolve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="iga_llm", description=__doc__.splitlines()[0])
    ap.add_argument("--tier", choices=TIERS, default="cheap")
    ap.add_argument("--model", help="explicit model id; overrides the tier map")
    ap.add_argument("--provider", choices=PROVIDERS, help="overrides IGA_PROVIDER")
    ap.add_argument("--system", help="system prompt")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds")
    ap.add_argument("--resolve", action="store_true", help="print the resolved provider/model and exit")
    args = ap.parse_args(argv)

    try:
        if args.resolve:
            provider, model = resolve(args.tier, model=args.model, provider=args.provider)
            print(json.dumps({"provider": provider, "model": model, "tier": args.tier}))
            return 0
        text = complete(
            sys.stdin.read(), tier=args.tier, model=args.model,
            system=args.system, timeout=args.timeout, provider=args.provider,
        )
    except LLMError as ex:
        print(f"iga_llm: {ex}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
