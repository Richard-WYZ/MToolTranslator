from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation.analysis import build_mtool_composition_plan
from translation.input import load_json_items
from translation.output import write_json_items


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an order-preserving MTool sample that covers multiline composition dependencies."
    )
    parser.add_argument("--file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", type=int, default=580)
    args = parser.parse_args()

    items = load_json_items(args.file)
    target_size = min(len(items), max(1, int(args.size)))
    plan = build_mtool_composition_plan(items)
    selected: set[int] = set()
    selected_parents: set[int] = set()

    for parent_index, entry in plan.entries.items():
        unit = {parent_index, *entry.dependency_indexes}
        if len(selected | unit) > target_size:
            continue
        selected.update(unit)
        selected_parents.add(parent_index)
        if len(selected) == target_size:
            break

    if len(selected) < target_size:
        for index, (key, _value) in enumerate(items):
            if index in selected or "\n" in str(key) or "\r" in str(key):
                continue
            selected.add(index)
            if len(selected) == target_size:
                break
    if len(selected) < target_size:
        for index in range(len(items)):
            selected.add(index)
            if len(selected) == target_size:
                break

    sampled_items = [item for index, item in enumerate(items) if index in selected]
    write_json_items(sampled_items, args.output)
    sampled_plan = build_mtool_composition_plan(sampled_items)
    print(json.dumps(
        {
            "source": str(Path(args.file)),
            "output": str(Path(args.output)),
            "entries": len(sampled_items),
            "selected_parent_units": len(selected_parents),
            "composable_entries": len(sampled_plan.entries),
            "context_children": len(sampled_plan.contexts_by_child),
            "same_source_order": [
                str(key) for index, (key, _value) in enumerate(items) if index in selected
            ] == [str(key) for key, _value in sampled_items],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
