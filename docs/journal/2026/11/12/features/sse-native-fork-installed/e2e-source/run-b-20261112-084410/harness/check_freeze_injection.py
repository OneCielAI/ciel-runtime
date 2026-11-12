"""Read-only: compare a product tree's main-injection code with the reviewed snapshot.

Usage: check_freeze_injection.py <product-root> [<reference-root>]
Reports bound expressions, the injected-text functions, Stop block headings and
side-marker counts. Prints JSON; never writes.
"""
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import raw_text_inventory as rti  # noqa: E402

HOOKS = 'ciel_runtime_support/claude_mods/ciel-work/hooks'
FUNCTIONS = {'side-intake.js': ['wakeText', 'line'], 'register.js': ['handoffView', 'sideReviewText', 'clipped']}


def fn(text, name):
    start = text.find('function ' + name + '(')
    if start < 0:
        return None
    return text[start:text.find('\n}\n', start)]


def main():
    product = Path(sys.argv[1])
    reference = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / 'snapshot-378f6d73'
    out = {'product': str(product), 'reference': str(reference), 'bounds': rti.verify_product_bounds(product), 'functions': {}}
    texts = {}
    for name, functions in FUNCTIONS.items():
        new = (product / HOOKS / name).read_text(encoding='utf-8')
        old = (reference / HOOKS / name).read_text(encoding='utf-8')
        texts[name] = (old, new)
        for function in functions:
            out['functions'][name + ':' + function] = 'same' if fn(old, function) == fn(new, function) else 'DIFF'
    pattern = re.compile(r"block: ?'([A-Z][^']{10,80})")
    out['stop_block_headings'] = {'reference': sorted(set(pattern.findall(texts['register.js'][0]))),
                                  'product': sorted(set(pattern.findall(texts['register.js'][1])))}
    markers = [rti.WAKE_MARKER, rti.REVIEW_MARKER, *rti.STOP_WAKE_MARKERS, rti.ADVICE_DATA]
    out['marker_counts'] = {m: [sum(t[0].count(m) for t in texts.values()), sum(t[1].count(m) for t in texts.values())] for m in markers}
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
