#!/usr/bin/env python3
"""Vendor the first-aid kit's skills from agent-almanac at a pinned commit.

Dev-time only: the plugin never fetches anything at runtime (catalog rule 3). The
script reads blobs with `git show <commit>:<path>`, so it never touches the
almanac checkout's working tree, index or HEAD.

    python3 scripts/sync_kit.py --almanac ../agent-almanac --commit <sha>
    python3 scripts/sync_kit.py --check                         # skills/ vs PROVENANCE.json
    python3 scripts/sync_kit.py --verify-source ../agent-almanac  # bytes vs the pinned commit

--check proves skills/ holds exactly the files PROVENANCE.json lists, unchanged. It
cannot prove where they came from: a hash recorded in the same commit only shows the
file was not changed after it. --verify-source closes that gap by re-reading every file
from an agent-almanac checkout (CI fetches the public repo at the pinned commit).

Only single-file skills (a lone SKILL.md) are vendored. A kit skill that gains
references/, scripts/ or other files at the almanac makes sync refuse it, because Hermes
serves such files to the model and each one would need its own review.

Maintainer tool, run by hand. The plugin never imports or runs it.
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


def _load_provenance(skills_dir: Path) -> dict | None:
    provenance_file = skills_dir / PROVENANCE_FILE.name
    if not provenance_file.is_file():
        return None
    return json.loads(provenance_file.read_text(encoding="utf-8"))


def check(skills_dir: Path = SKILLS_DIR) -> list[str]:
    """Return a list of problems; empty means skills/ holds exactly the listed files, unchanged."""
    problems: list[str] = []
    provenance = _load_provenance(skills_dir)
    if provenance is None:
        return [f"missing skills/{PROVENANCE_FILE.name}"]

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
        file_on_disk = skills_dir / relative_path
        if not file_on_disk.is_file():
            problems.append(f"missing skills/{relative_path}")
        elif _sha256(file_on_disk.read_bytes()) != recorded_hash:
            problems.append(f"skills/{relative_path} differs from the vendored copy (hash mismatch)")

    # Every file under skills/ must be accounted for: Hermes serves a plugin skill's
    # references/, scripts/, templates/ and assets/ files to the model.
    allowed_files = recorded_files | {PROVENANCE_FILE.name}
    for file_on_disk in sorted(skills_dir.rglob("*")):
        relative_path = file_on_disk.relative_to(skills_dir).as_posix()
        if (file_on_disk.is_file() or file_on_disk.is_symlink()) and relative_path not in allowed_files:
            problems.append(f"skills/{relative_path} is not listed in {PROVENANCE_FILE.name}")
    return problems


def verify_source(almanac_dir: Path, skills_dir: Path = SKILLS_DIR) -> list[str]:
    """Re-read every vendored file from `almanac_dir` at the recorded commit and compare bytes."""
    provenance = _load_provenance(skills_dir)
    if provenance is None:
        return [f"missing skills/{PROVENANCE_FILE.name}"]
    commit = provenance["commit"]
    problems: list[str] = []
    for relative_path in provenance.get("files", {}):
        source_path = "LICENSE" if relative_path == LICENSE_COPY_NAME else f"skills/{relative_path}"
        try:
            source_bytes = _git(almanac_dir, "show", f"{commit}:{source_path}")
        except subprocess.CalledProcessError as error:
            problems.append(f"{source_path} unreadable at {commit[:12]}: {error.stderr.decode().strip()}")
            continue
        file_on_disk = skills_dir / relative_path
        if not file_on_disk.is_file() or file_on_disk.read_bytes() != source_bytes:
            problems.append(f"skills/{relative_path} does not match {source_path} at {commit[:12]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--almanac", type=Path, help="path to an agent-almanac git checkout")
    parser.add_argument("--commit", help="almanac commit to vendor from (any rev; recorded as full SHA)")
    parser.add_argument("--check", action="store_true", help="verify skills/ against PROVENANCE.json")
    parser.add_argument("--verify-source", type=Path, metavar="ALMANAC",
                        help="compare skills/ byte for byte with an agent-almanac checkout at the pinned commit")
    args = parser.parse_args(argv)

    if args.check or args.verify_source:
        problems = check()
        if args.verify_source:
            problems += verify_source(args.verify_source)
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        if not problems:
            source_note = " and its source commit" if args.verify_source else ""
            print(f"OK kit matches PROVENANCE.json{source_note} ({len(kit.KIT_NAMES)} skills)")
        return 1 if problems else 0

    if not (args.almanac and args.commit):
        parser.error("--almanac and --commit are required unless --check is given")
    provenance = sync(args.almanac, args.commit)
    print(f"vendored {len(provenance['kit'])} skills from {provenance['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
