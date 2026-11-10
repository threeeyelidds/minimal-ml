"""Check the question bank: format, skill ids, coverage of every taxonomy skill.

    python tools/validate_bank.py            # all files
    python tools/validate_bank.py bank/llm.yml
"""

from __future__ import annotations

import pathlib
import re
import sys

try:
    import yaml
except ModuleNotFoundError:  # Quarto may run this with a Python outside the project venv
    import os
    _venv = pathlib.Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
    if _venv.exists() and not os.environ.get("PREP_REEXEC"):
        os.environ["PREP_REEXEC"] = "1"
        os.execv(str(_venv), [str(_venv)] + sys.argv)
    raise SystemExit("PyYAML is missing: run `uv sync` (or `pip install pyyaml`) and retry")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from taxonomy import DOMAINS  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOM_SKILLS = {d: [s[0] for s in skills] for d, _, skills in DOMAINS}
ALL_SKILLS = {s for v in DOM_SKILLS.values() for s in v}
TYPES = {"concept", "tradeoff", "design", "debug", "coding", "derivation", "behavioral", "case"}
LEVELS = {"core", "senior", "staff"}
TRACKS = {"mle", "ai", "re"}


def check(path: pathlib.Path, seen_ids: set) -> list[str]:
    errs = []
    try:
        d = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        return [f"YAML error: {e}"]
    dom = d.get("domain")
    if dom != "cases" and dom not in DOM_SKILLS:
        errs.append(f"unknown domain {dom!r}")
        return errs
    for k in ("title", "summary", "senior_bar", "rounds", "cards"):
        if not d.get(k):
            errs.append(f"missing top-level field {k}")
    for n in d.get("notes") or []:
        if not (ROOT / n).exists():
            errs.append(f"notes path does not exist: {n}")
    own = set(DOM_SKILLS.get(dom, []))
    covered = set()
    for c in d.get("cards") or []:
        cid = c.get("id", "?")
        for k in ("id", "skill", "type", "level", "tracks", "q", "a"):
            if not c.get(k):
                errs.append(f"{cid}: missing {k}")
        if not re.fullmatch(rf"{re.escape(dom)}-\d{{3}}", str(cid)):
            errs.append(f"{cid}: id must look like {dom}-001")
        if cid in seen_ids:
            errs.append(f"{cid}: duplicate id")
        seen_ids.add(cid)
        sk = c.get("skill")
        if sk not in ALL_SKILLS:
            errs.append(f"{cid}: unknown skill {sk!r}")
        elif dom != "cases" and sk not in own:
            errs.append(f"{cid}: skill {sk!r} is not in area {dom}")
        covered.add(sk)
        for s in c.get("also") or []:
            if s not in ALL_SKILLS:
                errs.append(f"{cid}: unknown skill in also: {s!r}")
        if c.get("type") not in TYPES:
            errs.append(f"{cid}: bad type {c.get('type')!r}")
        if c.get("level") not in LEVELS:
            errs.append(f"{cid}: bad level {c.get('level')!r}")
        if not set(c.get("tracks") or []) <= TRACKS or not c.get("tracks"):
            errs.append(f"{cid}: tracks must be a non-empty subset of {sorted(TRACKS)}")
        a = str(c.get("a") or "")
        words = len(a.split())
        if words < 40:
            errs.append(f"{cid}: answer too short ({words} words)")
        if words > 650:
            errs.append(f"{cid}: answer too long ({words} words)")
        if re.search(r"(?m)^#{1,6} ", re.sub(r"```.*?```", "", a, flags=re.S)):
            errs.append(f"{cid}: no Markdown headings in answers")
        if ":::" in a:
            errs.append(f"{cid}: no fenced divs in answers")
        if a.count("```") % 2:
            errs.append(f"{cid}: unbalanced code fence")
        nocode = re.sub(r"```.*?```|`[^`\n]*`", "", a, flags=re.S)
        if nocode.replace("\\$", "").replace("$$", "").count("$") % 2:
            errs.append(f"{cid}: unbalanced $ in answer")
    if dom != "cases":
        missing = own - covered
        if missing:
            errs.append(f"skills with no card: {sorted(missing)}")
        cl = {e.get("skill") for e in d.get("checklist") or []}
        if own - cl:
            errs.append(f"skills with no checklist entry: {sorted(own - cl)}")
        if cl - own:
            errs.append(f"checklist has skills outside the area: {sorted(cl - own)}")
    return errs


def main():
    paths = [pathlib.Path(p) for p in sys.argv[1:]] or sorted((ROOT / "bank").glob("*.yml"))
    seen, bad, total = set(), 0, 0
    for p in paths:
        errs = check(p, seen)
        d = yaml.safe_load(p.read_text()) if not any(e.startswith("YAML") for e in errs) else {}
        n = len(d.get("cards") or [])
        total += n
        print(f"{p.name:16s} {n:3d} cards  {'OK' if not errs else f'{len(errs)} problem(s)'}")
        for e in errs:
            print("   -", e)
        bad += bool(errs)
    if len(paths) > 1:
        have = {p.stem for p in paths}
        for dom in DOM_SKILLS:
            if dom not in have:
                print(f"missing bank file for area: {dom}")
                bad += 1
    print(f"{total} cards in {len(paths)} files; {'all good' if not bad else f'{bad} file(s) with problems'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
