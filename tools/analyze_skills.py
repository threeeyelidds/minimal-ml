"""Turn raw postings into the skill statistics the site and the report use.

Reads  data/raw/jobs_*.json   (full text, not committed)
Writes data/skills.json       per-skill demand, split by track / archetype / employer group / level, with short quotes
       data/postings.json     posting metadata + matched skill ids (no description text)
       data/summary.json      sample description, experience and pay figures

    python tools/analyze_skills.py
"""

from __future__ import annotations

import collections
import json
import pathlib
import random
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from taxonomy import DOMAINS  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"

BOILER = re.compile(
    r"equal opportunity|e-verify|race, |religion|veteran|disabilit|accommodation|gender identity|sexual orientation|"
    r"privacy (notice|policy|statement)|background check|fair chance|criminal histor|arrest|recruiting scam|"
    r"401\(?k\)?|dental|parental leave|pto\b|paid time off|wellness|stipend|health insurance|medical, |vision insurance|"
    r"visa sponsorship|work authorization|pay transparency|salary range|compensation (range|package)|base (salary|pay)|"
    r"export control|itar|personal data|applicant|how we'?re different|about (the company|us)\b|"
    r"do not discriminate|protected (class|characteristic|status)|legally protected",
    re.I,
)

# Titles the fetchers let through because a team name contains "machine learning" or "AI".
NOT_ML_TITLE = re.compile(
    r"\b(emir|soc|rtl|dft|asic|fpga|physical design|design verification|verification engineer|silicon|circuit\w*|analog|"
    r"layout|packag(e|ing)|thermal|signal integrity|power integrity|hardware|mechanical|firmware|pcb|eda|"
    r"in test|sdet|test engineer|quality assurance|qa|validation engineer|data ?cent(er|re) (technician|operations)|"
    r"network(ing)? engineer|site reliability|security (response|operations)|technical program|tpm|"
    r"support engineer|sales engineer|solutions? engineer|customer engineer)\b", re.I)

ML_DOMAINS = {"mlfund", "dl", "llm", "llmapp", "eval", "recsys", "infra", "serving", "rl", "research"}
GENERIC = {"evals", "metrics", "experimentation", "agents", "llm-general", "latency", "reliability", "gpu",
           "data-pipelines", "ml-observability", "search-ir", "retrieval", "papers", "research-taste", "prompting"}

ARCHETYPES = [
    ("forward-deployed", "Forward-deployed / customer-facing AI engineer",
     re.compile(r"forward[- ]deployed|\bfde\b|applied ai (engineer|architect)|client[- ]facing|deployed engineer|"
                r"deployment engineer|ai architect|agent engineer|ai deployment|implementation", re.I)),
    ("research-engineer", "Research engineer (training, post-training, evals, safety)", None),
    ("inference-perf", "Inference and performance engineer",
     re.compile(r"inference|performance|optimi[sz]ation|efficien|compiler|kernel|on[- ]device|runtime|serving|"
                r"accelerat|gpu|edge\b|embedded", re.I)),
    ("ml-infra", "ML platform / infrastructure engineer",
     re.compile(r"infrastructure|\binfra\b|platform|ml ?ops|mlops|training (systems?|platform|framework)|compute|"
                r"tooling|framework|distributed|data (infrastructure|platform|engine)|pipeline|systems engineer|"
                r"observability|reliability", re.I)),
    ("autonomy-robotics", "Autonomy / robotics / perception MLE",
     re.compile(r"perception|planning|planner|autonom|robot|simulation|prediction|manipulation|behavior|driving|"
                r"embodied|mapping|localization|sensor|vision|3d|world model|controls?\b|helix|locomotion", re.I)),
    ("recsys-search-ads", "Search / recommendation / ads / ranking MLE",
     re.compile(r"ranking|recommend|search|\bads?\b|advertis|relevance|feed|personali[sz]|discovery|marketplace|"
                r"growth|notification|retrieval|monetization|content understanding|shopping|homefeed|matching", re.I)),
    ("applied-ai-agents", "Applied AI / agent / LLM product engineer", None),
    ("product-mle", "Product / applied MLE (risk, fraud, pricing, forecasting, core ML)", None),
]
ARCH_NAME = {a[0]: a[1] for a in ARCHETYPES}
TRACK_NAME = {"mle": "Machine learning engineer", "ai-engineer": "AI engineer", "research-engineer": "Research engineer"}
GROUP_NAME = {"lab": "Frontier labs and model companies", "ai-startup": "AI-native product startups",
              "ai-infra": "AI infrastructure and compute", "big-tech": "Large and consumer technology companies",
              "robotics": "Robotics and autonomy", "quant": "Quant and trading",
              "enterprise": "Enterprises outside tech (finance, health, industry, retail)"}
LEVEL_NAME = {"senior": "Senior", "staff": "Staff / lead", "principal": "Principal / senior staff",
              "unlevelled": "No level in title"}


def clean(text: str) -> str:
    return "\n".join(l for l in text.split("\n") if not BOILER.search(l))


def norm_title(t: str) -> str:
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t.lower())
    t = re.sub(r"\s[-–—|,]\s*(remote|usa?|us|uk|emea|apac|canada|india|germany|france|london|new york|nyc|sf|san francisco|"
               r"seattle|bay area|tokyo|singapore|dublin|toronto|paris|berlin|zurich|bangalore|bengaluru|israel|poland).*$", "", t)
    return re.sub(r"[^a-z0-9+#/ ]+", " ", t).strip()


def track_of(j) -> str:
    t = j["track"]
    if t != "mts":
        return t
    title = j["title"].lower()
    if re.search(r"research|train|reinforcement|\brl\b|multimodal|model|eval|interp|safety|post|pre-", title):
        return "research-engineer"
    if re.search(r"inference|ml\b|machine learning|infra", title):
        return "mle"
    return "ai-engineer"


def archetype_of(j, track: str) -> str:
    hay = j["title"] + " | " + (j.get("team") or "")
    title = j["title"]
    if ARCHETYPES[0][2].search(title):
        return "forward-deployed"
    if track == "research-engineer":
        return "research-engineer"
    for aid in ("inference-perf", "ml-infra"):
        rx = next(a[2] for a in ARCHETYPES if a[0] == aid)
        if rx.search(title):
            return aid
    if j["group"] == "robotics" or next(a[2] for a in ARCHETYPES if a[0] == "autonomy-robotics").search(title):
        if j["group"] == "robotics" or not re.search(r"search|recommend|ranking|ads", title, re.I):
            if j["group"] == "robotics" or re.search(r"perception|autonom|robot|driving|embodied|manipulation|locomotion|sensor|3d", hay, re.I):
                return "autonomy-robotics"
    if next(a[2] for a in ARCHETYPES if a[0] == "recsys-search-ads").search(hay):
        return "recsys-search-ads"
    if track == "ai-engineer":
        return "applied-ai-agents"
    return "product-mle"


YOE = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b([^.\n]{0,90})", re.I)


def yoe_of(text: str):
    vals = []
    for m in YOE.finditer(text):
        n = int(m.group(1))
        ctx = m.group(2).lower()
        if 1 <= n <= 20 and re.search(r"experience|industry|professional|building|working|develop|engineer|hands-on|in (a|ml|ai|software)", ctx):
            vals.append(n)
    return max(vals) if vals else None


MONEY = re.compile(r"(?:US)?\$\s?(\d{2,3})(?:[,.](\d{3}))?(?:\.\d\d)?\s*(k)?\b", re.I)


def pay_of(text: str, comp: str = ""):
    nums = []
    for m in MONEY.finditer((comp or "") + "\n" + text):
        a, b, k = m.groups()
        if b:
            v = int(a) * 1000 + int(b)
        elif k:
            v = int(a) * 1000
        else:
            continue
        if 90_000 <= v <= 1_500_000:
            nums.append(v)
    if not nums:
        return None
    return [min(nums), max(nums)]


def is_us(loc: str) -> bool:
    return bool(re.search(r"\b(US|USA|United States|CA|NY|WA|TX|MA|San Francisco|New York|Seattle|Palo Alto|Mountain View|"
                          r"Sunnyvale|Menlo Park|Bay Area|Boston|Austin|Los Angeles|Remote - US|Bellevue|San Jose|Chicago|"
                          r"Pittsburgh|Foster City|Santa Clara|Redmond|Cupertino|Costa Mesa|Washington|Denver|Atlanta|San Mateo|"
                          r"Redwood City|San Diego|Irvine|Cambridge, MA|South San Francisco)\b", loc))


def load():
    jobs, meta = [], {}
    for p in sorted(RAW.glob("jobs_*.json")):
        d = json.loads(p.read_text())
        meta[p.name] = d.get("meta", {})
        jobs.extend(d["jobs"])
    return jobs, meta


def main():
    random.seed(7)
    raw, meta = load()
    compiled = [(dom_id, dom_name, [(sid, name, re.compile(rx, re.I)) for sid, name, rx in skills])
                for dom_id, dom_name, skills in DOMAINS]
    skill_dom = {sid: dom_id for dom_id, _, skills in compiled for sid, _, _ in skills}

    # one record per (company, normalised title); locations merged
    merged = {}
    for j in raw:
        key = (j["company"], norm_title(j["title"]))
        if key in merged:
            m = merged[key]
            if j.get("location") and j["location"] not in m["locations"]:
                m["locations"].append(j["location"])
            if len(j["text"]) > len(m["text"]):
                m["text"] = j["text"]
            continue
        m = dict(j)
        m["locations"] = [j["location"]] if j.get("location") else []
        merged[key] = m

    posts, dropped = [], collections.Counter()
    for m in merged.values():
        if NOT_ML_TITLE.search(m["title"]):
            dropped["hardware, test or support role"] += 1
            continue
        text = clean(m["text"])
        track = track_of(m)
        hits = {sid for _, _, skills in compiled for sid, _, rx in skills if rx.search(text)}
        ml_hits = {s for s in hits if skill_dom[s] in ML_DOMAINS and s not in GENERIC}
        if len(ml_hits) < 3:
            dropped["not an ML role by content"] += 1
            continue
        yoe = yoe_of(text)
        level = m["level"]
        if level == "mid" or (level == "unlevelled" and yoe is not None and yoe <= 2):
            dropped["below senior"] += 1
            continue
        if len(text) < 600:
            dropped["description too short"] += 1
            continue
        pay = pay_of(m["text"], m.get("comp", ""))
        posts.append(dict(
            id=len(posts), title=m["title"], company=m["company"], group=m["group"], track=track,
            archetype=archetype_of(m, track), level=level, yoe=yoe, pay=pay,
            locations=m["locations"][:6], us=any(is_us(l) for l in m["locations"]),
            url=m["url"], posted=m.get("posted", ""), team=m.get("team", ""), source=m.get("source", ""),
            skills=sorted(hits), _text=text,
            phd=bool(re.search(r"\bph\.?d\b|\bdoctorate\b", text, re.I)),
        ))

    n = len(posts)
    by = lambda key: collections.Counter(p[key] for p in posts)  # noqa: E731
    dims = dict(track=by("track"), archetype=by("archetype"), group=by("group"), level=by("level"))

    def share(sid, key=None, val=None):
        sel = posts if key is None else [p for p in posts if p[key] == val]
        return round(100 * sum(sid in p["skills"] for p in sel) / max(1, len(sel)), 1)

    def quotes(sid, rx, k=6):
        cands = []
        for p in posts:
            for line in p["_text"].split("\n"):
                line = line.strip(" -•*\t")
                if 45 <= len(line) <= 230 and rx.search(line) and not line.endswith(":"):
                    cands.append((p, line))
                    break
        random.shuffle(cands)
        out, seen = [], set()
        for p, line in cands:
            if p["company"] in seen:
                continue
            seen.add(p["company"])
            out.append(dict(company=p["company"], title=p["title"], url=p["url"], text=line))
            if len(out) == k:
                break
        return out

    domains_out = []
    for dom_id, dom_name, skills in compiled:
        sk = []
        for sid, name, rx in skills:
            cnt = sum(sid in p["skills"] for p in posts)
            comp = collections.Counter(p["company"] for p in posts if sid in p["skills"])
            sk.append(dict(
                id=sid, name=name, n=cnt, pct=share(sid),
                by_track={t: share(sid, "track", t) for t in dims["track"]},
                by_archetype={a: share(sid, "archetype", a) for a in dims["archetype"]},
                by_group={g: share(sid, "group", g) for g in dims["group"]},
                by_level={l: share(sid, "level", l) for l in dims["level"]},
                companies=[c for c, _ in comp.most_common(8)],
                quotes=quotes(sid, rx),
            ))
        sk.sort(key=lambda s: -s["n"])
        any_dom = round(100 * sum(any(skill_dom[s] == dom_id for s in p["skills"]) for p in posts) / n, 1)
        domains_out.append(dict(id=dom_id, name=dom_name, pct_any=any_dom, skills=sk))

    yoes = [p["yoe"] for p in posts if p["yoe"]]
    pay_rows = [p for p in posts if p["pay"] and p["us"]]

    def pay_stats(sel):
        if len(sel) < 5:
            return None
        lo = sorted(p["pay"][0] for p in sel)
        hi = sorted(p["pay"][1] for p in sel)
        return dict(n=len(sel), low_median=int(statistics.median(lo)), high_median=int(statistics.median(hi)),
                    high_p90=hi[int(0.9 * (len(hi) - 1))])

    summary = dict(
        fetched={k: v.get("fetched") for k, v in meta.items()},
        raw_postings=len(raw), unique_postings=len(merged), analysed=n, dropped=dict(dropped),
        companies=len({p["company"] for p in posts}),
        dims={k: dict(v.most_common()) for k, v in dims.items()},
        names=dict(track=TRACK_NAME, archetype=ARCH_NAME, group=GROUP_NAME, level=LEVEL_NAME),
        us_share=round(100 * sum(p["us"] for p in posts) / n, 1),
        phd_share=round(100 * sum(p["phd"] for p in posts) / n, 1),
        phd_by_track={t: round(100 * sum(p["phd"] for p in posts if p["track"] == t) / c, 1) for t, c in dims["track"].items()},
        yoe=dict(stated=len(yoes), share_stated=round(100 * len(yoes) / n, 1), median=statistics.median(yoes),
                 hist=dict(sorted(collections.Counter(min(y, 12) for y in yoes).items())),
                 by_level={l: statistics.median([p["yoe"] for p in posts if p["yoe"] and p["level"] == l] or [0]) for l in dims["level"]}),
        pay=dict(note="Posted US base-pay ranges as written in the posting; equity and bonus excluded.",
                 all=pay_stats(pay_rows),
                 by_level={l: pay_stats([p for p in pay_rows if p["level"] == l]) for l in dims["level"]},
                 by_group={g: pay_stats([p for p in pay_rows if p["group"] == g]) for g in dims["group"]},
                 by_track={t: pay_stats([p for p in pay_rows if p["track"] == t]) for t in dims["track"]}),
        top_companies=collections.Counter(p["company"] for p in posts).most_common(60),
        posted_month=dict(sorted(collections.Counter((p["posted"] or "unknown")[:7] for p in posts).items())),
        skills_per_posting=statistics.median(len(p["skills"]) for p in posts),
        boards=sum(len(v.get("boards", {})) + len(v.get("sources", {})) for v in meta.values()),
    )

    for p in posts:
        del p["_text"]
    (OUT / "skills.json").write_text(json.dumps(dict(n=n, domains=domains_out), ensure_ascii=False, indent=1))
    (OUT / "postings.json").write_text(json.dumps(posts, ensure_ascii=False))
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    print(f"raw {len(raw)} -> unique {len(merged)} -> analysed {n}; dropped {dict(dropped)}")
    for k, v in dims.items():
        print(k, dict(v.most_common()))
    print("yoe median", summary["yoe"]["median"], summary["yoe"]["by_level"], "stated", summary["yoe"]["share_stated"])
    print("pay", summary["pay"]["all"], summary["pay"]["by_level"])
    for d in domains_out:
        print(f"\n## {d['name']}  (any: {d['pct_any']}%)")
        for s in d["skills"]:
            bt = s["by_track"]
            print(f"  {s['pct']:5.1f}%  {s['name'][:62]:62s} mle {bt.get('mle', 0):5.1f} ai {bt.get('ai-engineer', 0):5.1f} re {bt.get('research-engineer', 0):5.1f}")


if __name__ == "__main__":
    main()
