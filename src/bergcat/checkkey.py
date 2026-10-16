"""Confirm the API key is visible to the project, without spending anything.

    python -m bergcat.checkkey            # is a key present, and where from?
    python -m bergcat.checkkey --verify   # also make one tiny live call

The plain form touches no network. --verify sends a ~10-token request (well
under a tenth of a cent) purely to prove the key is accepted.
"""
from __future__ import annotations

import argparse
import os

from . import config


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="make one minimal live API call (costs ~$0.0001)")
    args = ap.parse_args()

    env_file = config.ROOT / ".env"
    key = os.environ.get("ANTHROPIC_API_KEY")

    print(f"  .env file      : {'found' if env_file.exists() else 'not present'}")
    if not key:
        print("  ANTHROPIC_API_KEY: NOT FOUND\n")
        try:
            config.api_key()
        except RuntimeError as e:
            print(e)
        return 1

    # Never print the key. Shape and length are enough to spot a paste error.
    print(f"  ANTHROPIC_API_KEY: found ({len(key)} chars, "
          f"starts {key[:7]}…, ends …{key[-4:]})")
    if not key.startswith("sk-ant-"):
        print("  WARNING: keys normally start 'sk-ant-'. Check for a bad paste.")

    if not args.verify:
        print("\n  Looks wired up. Add --verify to confirm the API accepts it.")
        return 0

    try:
        import anthropic
    except ImportError:
        print("\n  anthropic SDK not installed in this interpreter.")
        return 1

    print(f"\n  Calling {config.MODEL} with a minimal request…")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the word: ready"}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        print(f"  OK — model replied: {text.strip()!r}")
        print(f"  tokens in/out: {resp.usage.input_tokens}/{resp.usage.output_tokens}")
        return 0
    except anthropic.AuthenticationError:
        print("  FAILED: key rejected. Check it at "
              "https://console.anthropic.com/settings/keys")
    except anthropic.PermissionDeniedError:
        print("  FAILED: key valid but lacks permission for this model.")
    except anthropic.APIStatusError as e:
        # Credit exhaustion surfaces here rather than as an auth failure.
        print(f"  FAILED: HTTP {e.status_code} — {e.message}")
        if e.status_code == 400 and "credit" in str(e.message).lower():
            print("  Looks like a billing/credit issue rather than a bad key.")
    except anthropic.APIConnectionError:
        print("  FAILED: could not reach the API. Check network/proxy.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
