"""Validate the translate prompt against a set of English sentences.

Usage:
    uv run python scripts/validate_prompt.py [path/to/sentences.txt]

Each line is an English sentence. The script prints the returned JSON with
char_start/char_end offsets computed, plus a sanity check on token coverage.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make sibling `app` importable when run from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.pipeline import (  # noqa: E402
    _attach_char_offsets,
    _backfill_trailing_punct,
    _drop_empty_cards,
    _merge_apostrophe_clitics,
    call_translate_llm,
)

# Mistral free-tier rate limit is ~1 RPS. Use a polite sleep so batch
# validation doesn't get 429'd. Adjust if Mistral changes their limits.
RATE_LIMIT_SLEEP_SEC = 2.0


DEFAULT_SENTENCES = [
    "I'd like the bill, please.",
    "Could you say that one more time, slower?",
    "Where's the nearest train station from here?",
    "I don't eat meat, but I love seafood.",
    "How much does this cost in total?",
    "What time does the museum close tonight?",
    "Sorry, I didn't catch your name.",
    "Can I pay by card or only cash?",
    "I've been studying Italian for about six months.",
    "She said she'd meet us at the cafe at seven.",
    "We're staying at a small place near the river.",
    "Could I get a coffee with a little milk, not too much?",
    "It's raining harder than I expected.",
    "Do you have anything that's not too spicy?",
    "I think I left my phone in the taxi.",
    "Would you mind opening the window a bit?",
    "Is there a bathroom I can use here?",
    "Let's split the bill four ways.",
    "I've never been here before — what do you recommend?",
    "Excuse me, is this seat taken?",
]


async def run(sentences: list[str]) -> None:
    print(f"Using model: {settings.mistral_llm_model} (Mistral)\n")

    for i, en in enumerate(sentences, 1):
        if i > 1:
            await asyncio.sleep(RATE_LIMIT_SLEEP_SEC)
        print(f"\n=== {i}. {en}")
        try:
            data = await call_translate_llm(en)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate_limited" in msg:
                print(f"  ⏳ 429 — sleeping 30s and retrying once")
                await asyncio.sleep(30)
                try:
                    data = await call_translate_llm(en)
                except Exception as e2:
                    print(f"  !! retry failed: {e2}")
                    continue
            else:
                print(f"  !! call failed: {e}")
                continue

        target = data.get("target_text", "")
        tokens = data.get("tokens", [])
        tokens = _merge_apostrophe_clitics(tokens)
        tokens = _backfill_trailing_punct(target, tokens)
        tokens = _attach_char_offsets(target, tokens)
        cards = _drop_empty_cards(data.get("cards", []))

        # Coverage check: reconstruct target_text from tokens.
        rebuilt = ""
        for t in tokens:
            start = t.get("char_start", len(rebuilt))
            if start > len(rebuilt):
                rebuilt += target[len(rebuilt):start]
            rebuilt += t.get("surface", "")
        coverage = (
            "OK" if rebuilt.strip() == target.strip() else f"MISMATCH: {rebuilt!r} vs {target!r}"
        )

        print(f"  it: {target}")
        print(f"  tokens ({len(tokens)}): coverage={coverage}")
        for t in tokens:
            if t.get("is_word", True):
                print(
                    f"    [{t.get('char_start',0):>3}..{t.get('char_end',0):>3}] "
                    f"{t.get('surface',''):<14} {t.get('lemma',''):<14} "
                    f"{t.get('pos',''):<6} → {t.get('gloss','')}"
                )
        print(f"  cards ({len(cards)}):")
        for c in cards:
            print(f"    {c.get('front','')[:40]:<40} ⇢ {c.get('back','')}  [{c.get('pos','')}]")


def main() -> None:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        sentences = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    else:
        sentences = DEFAULT_SENTENCES
    asyncio.run(run(sentences))


if __name__ == "__main__":
    main()
