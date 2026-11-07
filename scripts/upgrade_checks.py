#!/usr/bin/env python3
"""
Checks/fixups used by sync-deploy.sh and upgrade-preflight.sh.

Runs against the repository in the current working directory and only uses
the standard library, so it works without the project's virtualenv.

Subcommands:
  dedupe-deps     Drop later duplicate pyproject.toml dependency declarations
                  (keep the topmost). Prints removed lines; exit 0 either way.
  alembic-heads   Print alembic heads; exit 1 if there is more than one.
  enum-dupes [REF]
                  Find Enum classes in app/ with duplicate values (Python
                  silently turns those into aliases); exit 1 if any. With REF
                  (e.g. the upstream tag), duplicates already present in REF
                  are ignored - upstream has a few intentional aliases.
  all [REF]       alembic-heads + enum-dupes, exit 1 if any fails.
"""

import ast
import glob
import re
import subprocess
import sys


def dedupe_deps(path="pyproject.toml") -> int:
    with open(path) as f:
        lines = f.readlines()

    start = end = None
    for i, line in enumerate(lines):
        if start is None and line.strip().startswith("dependencies = ["):
            start = i
        elif start is not None and line.strip() == "]":
            end = i
            break
    if start is None or end is None:
        return 0

    name_re = re.compile(r'^\s*"([A-Za-z0-9_.\-]+)')
    seen, out, removed = set(), [], []
    for i, line in enumerate(lines):
        if start < i < end:
            m = name_re.match(line)
            if m:
                # no PEP 503 normalisation on purpose: upstream itself declares both
                # "sqlalchemy_utils" and "sqlalchemy-utils"; leave that alone.
                name = m.group(1).lower()
                if name in seen:
                    removed.append(line.strip())
                    continue
                seen.add(name)
        out.append(line)

    if removed:
        with open(path, "w") as f:
            f.writelines(out)
        for r in removed:
            print(r)
    return 0


def alembic_heads(quiet=False) -> int:
    revs, downs = {}, set()
    for f in glob.glob("migrations/versions/*.py"):
        with open(f) as fh:
            s = fh.read()
        r = re.search(r"^revision\s*=\s*['\"](\w+)['\"]", s, re.M)
        d = re.search(r"^down_revision\s*=\s*(.+)$", s, re.M)
        if not r:
            continue
        revs[r.group(1)] = f
        if d:
            downs.update(re.findall(r"['\"](\w+)['\"]", d.group(1)))
    heads = sorted((r, f) for r, f in revs.items() if r not in downs)
    if not quiet or len(heads) != 1:
        print(f"alembic heads ({len(heads)}):")
        for r, f in heads:
            print(f"  {r}  {f}")
    return 0 if len(heads) == 1 else 1


def _enum_dupes_in(path, source):
    found = []
    try:
        tree = ast.parse(source, path)
    except SyntaxError as e:
        return [(path, "<parse error>", str(e), None, None, None)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any("Enum" in ast.unparse(b) for b in node.bases):
            continue
        seen = {}
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant)
            ):
                name, value = stmt.targets[0].id, stmt.value.value
                if value in seen:
                    found.append((path, node.name, name, value, seen[value], stmt.lineno))
                else:
                    seen[value] = name
    return found


def _enum_dupes_worktree():
    found = []
    for f in sorted(glob.glob("app/**/*.py", recursive=True)):
        with open(f) as fh:
            found += _enum_dupes_in(f, fh.read())
    return found


def _enum_dupes_ref(ref):
    files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "app/"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    found = []
    for f in files:
        if f.endswith(".py"):
            src = subprocess.run(
                ["git", "show", f"{ref}:{f}"], check=True, capture_output=True, text=True
            ).stdout
            found += _enum_dupes_in(f, src)
    return found


def enum_dupes(baseline_ref=None) -> int:
    def key(d):  # ignore line numbers
        return d[:5]

    known = {key(d) for d in _enum_dupes_ref(baseline_ref)} if baseline_ref else set()
    problems = [d for d in _enum_dupes_worktree() if key(d) not in known]
    for f, cls, name, value, other, line in problems:
        print(f"{f}:{line}: {cls}.{name} = {value!r} duplicates {cls}.{other}")
    if not problems:
        suffix = f" (beyond those already in {baseline_ref})" if baseline_ref else ""
        print(f"enum values: no duplicates{suffix}")
    return 1 if problems else 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "dedupe-deps":
        return dedupe_deps()
    if cmd == "alembic-heads":
        return alembic_heads()
    ref = argv[2] if len(argv) > 2 else None
    if cmd == "enum-dupes":
        return enum_dupes(ref)
    if cmd == "all":
        return alembic_heads() | enum_dupes(ref)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
