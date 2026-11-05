#!/usr/bin/env python3
"""Vendor the first-aid kit's skills from agent-almanac at a pinned commit.

Dev-time only: the plugin never fetches anything at runtime (catalog rule 3). The
script reads blobs with `git show <commit>:<path>`, so it never touches the
almanac checkout's working tree, index or HEAD.

    python3 scripts/sync_kit.py --almanac ../agent-almanac --commit <sha>
    python3 scripts/sync_kit.py --check     # verify skills/ against PROVENANCE.json

Run from the repository root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import kit  # noqa: E402  (needs the repo root on sys.path)

SKILLS_DIR = REPO_ROOT / "skills"
PROVENANCE_FILE = SKILLS_DIR / "PROVENANCE.json"
LICENSE_COPY_NAME = "LICENSE-agent-almanac"
ALMANAC_URL = "https://github.com/pjt222/agent-almanac"


def _git(almanac_dir: Path, *git_args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(almanac_dir), *git_args], check=True, capture_output=True
    )
    return completed.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sync(almanac_dir: Path, commit: str) -> dict:
    """Copy every kit skill and the almanac LICENSE at `commit`; write PROVENANCE.json."""
    full_commit = _git(almanac_dir, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()

    unexpected_dirs = sorted(
        child.name for child in SKILLS_DIR.iterdir()
        if child.is_dir() and child.name not in kit.KIT_NAMES
    ) if SKILLS_DIR.exists() else []
    if unexpected_dirs:
        raise SystemExit(f"skills/ holds directories outside the kit, remove them first: {unexpected_dirs}")

    file_hashes: dict[str, str] = {}
    for practice_name in kit.KIT_NAMES:
        tree_listing = _git(
            almanac_dir, "ls-tree", "-r", "--name-only", full_commit, f"skills/{practice_name}/"
        ).decode().split()
        if tree_listing != [f"skills/{practice_name}/SKILL.md"]:
            raise SystemExit(
                f"{practice_name}: expected exactly one SKILL.md at {full_commit[:12]}, found {tree_listing}"
            )
        skill_bytes = _git(almanac_dir, "show", f"{full_commit}:skills/{practice_name}/SKILL.md")
        target = SKILLS_DIR / practice_name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(skill_bytes)
        file_hashes[f"{practice_name}/SKILL.md"] = _sha256(skill_bytes)

    license_bytes = _git(almanac_dir, "show", f"{full_commit}:LICENSE")
    (SKILLS_DIR / LICENSE_COPY_NAME).write_bytes(license_bytes)
    file_hashes[LICENSE_COPY_NAME] = _sha256(license_bytes)

    provenance = {
        "source": ALMANAC_URL,
        "commit": full_commit,
        "kit": list(kit.KIT_NAMES),
        "files": dict(sorted(file_hashes.items())),
    }
    PROVENANCE_FILE.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8", newline="\n")
    return provenance


def check() -> list[str]:
    """Return a list of problems; empty means skills/ matches PROVENANCE.json and kit.KIT."""
    problems: list[str] = []
    if not PROVENANCE_FILE.is_file():
        return [f"missing {PROVENANCE_FILE.relative_to(REPO_ROOT)}"]
    provenance = json.loads(PROVENANCE_FILE.read_text(encoding="utf-8"))

    if provenance.get("kit") != list(kit.KIT_NAMES):
        problems.append(f"PROVENANCE kit {provenance.get('kit')} != kit.KIT_NAMES {list(kit.KIT_NAMES)}")
    commit = provenance.get("commit", "")
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        problems.append(f"PROVENANCE commit is not a full 40-hex SHA: {commit!r}")

    expected_files = {f"{name}/SKILL.md" for name in kit.KIT_NAMES} | {LICENSE_COPY_NAME}
    recorded_files = set(provenance.get("files", {}))
    if recorded_files != expected_files:
        problems.append(f"PROVENANCE files differ from the kit: {sorted(recorded_files ^ expected_files)}")

    for relative_path, recorded_hash in provenance.get("files", {}).items():
        file_on_disk = SKILLS_DIR / relative_path
        if not file_on_disk.is_file():
            problems.append(f"missing skills/{relative_path}")
        elif _sha256(file_on_disk.read_bytes()) != recorded_hash:
            problems.append(f"skills/{relative_path} differs from the vendored copy (hash mismatch)")

    stray_dirs = sorted(
        child.name for child in SKILLS_DIR.iterdir() if child.is_dir() and child.name not in kit.KIT_NAMES
    )
    if stray_dirs:
        problems.append(f"skills/ holds directories outside the kit: {stray_dirs}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--almanac", type=Path, help="path to an agent-almanac git checkout")
    parser.add_argument("--commit", help="almanac commit to vendor from (any rev; recorded as full SHA)")
    parser.add_argument("--check", action="store_true", help="verify skills/ against PROVENANCE.json")
    args = parser.parse_args(argv)

    if args.check:
        problems = check()
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        if not problems:
            print(f"OK kit matches PROVENANCE.json ({len(kit.KIT_NAMES)} skills)")
        return 1 if problems else 0

    if not (args.almanac and args.commit):
        parser.error("--almanac and --commit are required unless --check is given")
    provenance = sync(args.almanac, args.commit)
    print(f"vendored {len(provenance['kit'])} skills from {provenance['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
