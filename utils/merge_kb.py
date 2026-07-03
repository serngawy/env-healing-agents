"""
Merge numbered knowledge-base ConfigMap chunks into single JSON files.

Expected CMS layout (each chunk is a ConfigMap mounted as a directory):
  <cms_dir>/known-issues/<N>/data.json       → known_issues.json   (merge "patterns" arrays)
  <cms_dir>/fix-strategies/<N>/data.json     → fix_strategies.json (merge "fix_strategies" dicts)
  <cms_dir>/remediation-outcomes/<N>/data.json → remediation_outcomes.json (concatenate arrays)

Output: <kb_dir>/{known_issues,fix_strategies,remediation_outcomes}.json

To add a new chunk: create a ConfigMap, mount it at the next numbered path, and
restart the pod — no changes to this module needed.
"""

import glob
import json
import os


def _load_chunks(pattern):
    paths = sorted(glob.glob(pattern))
    if not paths:
        print(f"  [warn] no files matched {pattern}", flush=True)
    chunks = []
    for p in paths:
        with open(p) as fh:
            chunks.append((p, json.load(fh)))
    return chunks


def _write(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2)
    print(f"  wrote {path} ({os.path.getsize(path)} bytes)", flush=True)


def merge_knowledge_base(cms_dir: str, kb_dir: str) -> None:
    os.makedirs(kb_dir, exist_ok=True)

    # known_issues.json — merge "patterns" arrays across chunks
    print("Merging known_issues.json ...", flush=True)
    chunks = _load_chunks(f"{cms_dir}/known-issues/*/data.json")
    merged: dict = {}
    for path, chunk in chunks:
        for key, val in chunk.items():
            if key == "patterns":
                merged.setdefault("patterns", []).extend(val)
            else:
                merged[key] = val
        print(f"  + {path}: {len(chunk.get('patterns', []))} pattern(s)", flush=True)
    _write(f"{kb_dir}/known_issues.json", merged)
    print(f"  total patterns: {len(merged.get('patterns', []))}", flush=True)

    # fix_strategies.json — merge "fix_strategies" dicts across chunks
    print("Merging fix_strategies.json ...", flush=True)
    chunks = _load_chunks(f"{cms_dir}/fix-strategies/*/data.json")
    merged = {}
    for path, chunk in chunks:
        for key, val in chunk.items():
            if key == "fix_strategies":
                merged.setdefault("fix_strategies", {}).update(val)
            else:
                merged[key] = val
        print(f"  + {path}: {len(chunk.get('fix_strategies', {}))} strategy/strategies", flush=True)
    _write(f"{kb_dir}/fix_strategies.json", merged)
    print(f"  total strategies: {len(merged.get('fix_strategies', {}))}", flush=True)

    # remediation_outcomes.json — concatenate arrays across chunks
    print("Merging remediation_outcomes.json ...", flush=True)
    chunks = _load_chunks(f"{cms_dir}/remediation-outcomes/*/data.json")
    merged_list: list = []
    for path, chunk in chunks:
        if isinstance(chunk, list):
            merged_list.extend(chunk)
            print(f"  + {path}: {len(chunk)} entry/entries", flush=True)
        else:
            print(f"  [warn] {path} is not a JSON array, skipping", flush=True)
    _write(f"{kb_dir}/remediation_outcomes.json", merged_list)
    print(f"  total outcomes: {len(merged_list)}", flush=True)

    print("Knowledge base merge complete.", flush=True)
