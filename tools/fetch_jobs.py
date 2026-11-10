"""Fetch currently open MLE / AI engineer / research engineer postings.

Sources are the public job-board APIs of Greenhouse, Ashby and Lever (no login,
no scraping of rendered pages). Each company below is probed on all three and
whichever answers is used.

Output: data/raw/jobs_ats.json (full description text; gitignored, the text
belongs to the employers) -- one record per posting.

    python tools/fetch_jobs.py
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import html
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = "Mozilla/5.0 (compatible; job-board-reader/1.0)"

# name -> (group, candidate board slugs). Slugs are guesses; the first that
# answers on any system wins.
COMPANIES: dict[str, tuple[str, list[str]]] = {
    # frontier labs / model companies
    "Anthropic": ("lab", ["anthropic"]),
    "OpenAI": ("lab", ["openai"]),
    "Google DeepMind": ("lab", ["deepmind"]),
    "xAI": ("lab", ["xai"]),
    "Mistral AI": ("lab", ["mistral", "mistralai"]),
    "Cohere": ("lab", ["cohere"]),
    "Thinking Machines": ("lab", ["thinkingmachines", "thinking-machines", "tml"]),
    "Safe Superintelligence": ("lab", ["ssi", "safesuperintelligence"]),
    "Reflection AI": ("lab", ["reflectionai", "reflection"]),
    "AI21": ("lab", ["ai21", "ai21labs"]),
    "Reka": ("lab", ["reka", "rekaai"]),
    "Poolside": ("lab", ["poolside", "poolsideai"]),
    "Magic": ("lab", ["magic", "magic.dev", "magicdev"]),
    "Imbue": ("lab", ["imbue", "generallyintelligent"]),
    "Isomorphic Labs": ("lab", ["isomorphiclabs"]),
    "Black Forest Labs": ("lab", ["blackforestlabs", "bfl"]),
    "Runway": ("lab", ["runwayml", "runway"]),
    "Luma AI": ("lab", ["lumalabs", "luma", "luma-ai"]),
    "Pika": ("lab", ["pika", "pikalabs"]),
    "Suno": ("lab", ["suno"]),
    "ElevenLabs": ("lab", ["elevenlabs"]),
    "Character.AI": ("lab", ["character", "characterai"]),
    "Midjourney": ("lab", ["midjourney"]),
    "Stability AI": ("lab", ["stabilityai", "stability"]),
    "Adept": ("lab", ["adept"]),
    "Liquid AI": ("lab", ["liquidai", "liquid"]),
    "Essential AI": ("lab", ["essentialai", "essential"]),
    "Periodic Labs": ("lab", ["periodiclabs", "periodic"]),
    "Goodfire": ("lab", ["goodfire"]),
    "METR": ("lab", ["metr"]),
    "Apollo Research": ("lab", ["apolloresearch"]),
    "World Labs": ("lab", ["worldlabs"]),
    "Sakana AI": ("lab", ["sakana", "sakanaai"]),
    "DeepL": ("lab", ["deepl"]),
    "Perplexity": ("lab", ["perplexity", "perplexityai"]),
    # AI-native products / agents / applied AI startups
    "Cursor (Anysphere)": ("ai-startup", ["cursor", "anysphere"]),
    "Cognition": ("ai-startup", ["cognition", "cognitionlabs"]),
    "Harvey": ("ai-startup", ["harvey"]),
    "Sierra": ("ai-startup", ["sierra"]),
    "Decagon": ("ai-startup", ["decagon"]),
    "Glean": ("ai-startup", ["gleanwork", "glean"]),
    "Hebbia": ("ai-startup", ["hebbia"]),
    "Abridge": ("ai-startup", ["abridge"]),
    "OpenEvidence": ("ai-startup", ["openevidence"]),
    "Hippocratic AI": ("ai-startup", ["hippocraticai", "hippocratic"]),
    "Ambience": ("ai-startup", ["ambiencehealthcare", "ambience"]),
    "Writer": ("ai-startup", ["writer"]),
    "Jasper": ("ai-startup", ["jasper"]),
    "Replit": ("ai-startup", ["replit"]),
    "Lovable": ("ai-startup", ["lovable"]),
    "Vercel": ("ai-startup", ["vercel"]),
    "Sourcegraph": ("ai-startup", ["sourcegraph", "sourcegraph91"]),
    "Codeium / Windsurf": ("ai-startup", ["codeium", "windsurf", "exafunction"]),
    "Augment Code": ("ai-startup", ["augmentcode", "augment", "augmentcomputing"]),
    "Mercor": ("ai-startup", ["mercor"]),
    "Clay": ("ai-startup", ["clay", "claylabs"]),
    "Ramp": ("ai-startup", ["ramp"]),
    "Notion": ("ai-startup", ["notion"]),
    "Linear": ("ai-startup", ["linear"]),
    "Granola": ("ai-startup", ["granola"]),
    "Speak": ("ai-startup", ["speak"]),
    "Synthesia": ("ai-startup", ["synthesia"]),
    "HeyGen": ("ai-startup", ["heygen"]),
    "Captions": ("ai-startup", ["captions"]),
    "Exa": ("ai-startup", ["exa", "exaai"]),
    "You.com": ("ai-startup", ["you", "youcom"]),
    "Contextual AI": ("ai-startup", ["contextualai", "contextual"]),
    "LangChain": ("ai-startup", ["langchain"]),
    "LlamaIndex": ("ai-startup", ["llamaindex"]),
    "Pinecone": ("ai-startup", ["pinecone"]),
    "Weaviate": ("ai-startup", ["weaviate"]),
    "Snorkel AI": ("ai-startup", ["snorkelai", "snorkel"]),
    "Scale AI": ("ai-startup", ["scaleai", "scale"]),
    "Surge AI": ("ai-startup", ["surgeai", "surge"]),
    "Labelbox": ("ai-startup", ["labelbox"]),
    "Arize": ("ai-startup", ["arizeai", "arize"]),
    "Braintrust": ("ai-startup", ["braintrust", "braintrustdata"]),
    "Weights & Biases": ("ai-startup", ["wandb"]),
    "Intercom": ("ai-startup", ["intercom"]),
    "Grammarly": ("ai-startup", ["grammarly"]),
    "Duolingo": ("ai-startup", ["duolingo"]),
    "Figma": ("ai-startup", ["figma"]),
    "Canva": ("ai-startup", ["canva"]),
    "Rippling": ("ai-startup", ["rippling"]),
    "Moveworks": ("ai-startup", ["moveworks"]),
    "Cresta": ("ai-startup", ["cresta"]),
    "Observe.AI": ("ai-startup", ["observeai"]),
    "Tavus": ("ai-startup", ["tavus"]),
    "Cartesia": ("ai-startup", ["cartesia"]),
    "Deepgram": ("ai-startup", ["deepgram"]),
    "AssemblyAI": ("ai-startup", ["assemblyai"]),
    "Twelve Labs": ("ai-startup", ["twelvelabs", "twelve-labs"]),
    "Typeface": ("ai-startup", ["typeface"]),
    "Distyl": ("ai-startup", ["distyl", "distylai"]),
    "Sesame": ("ai-startup", ["sesame", "sesameai"]),
    "Fal": ("ai-startup", ["fal", "falai"]),
    "Lila Sciences": ("ai-startup", ["lilasciences", "lila"]),
    "EvenUp": ("ai-startup", ["evenup"]),
    "Chai Discovery": ("ai-startup", ["chaidiscovery"]),
    "Genesis Therapeutics": ("ai-startup", ["genesistherapeutics"]),
    # AI infra / compute / inference
    "Together AI": ("ai-infra", ["togetherai", "together"]),
    "Fireworks AI": ("ai-infra", ["fireworksai", "fireworks"]),
    "Baseten": ("ai-infra", ["baseten"]),
    "Modal": ("ai-infra", ["modal", "modallabs"]),
    "Replicate": ("ai-infra", ["replicate"]),
    "Anyscale": ("ai-infra", ["anyscale"]),
    "Lambda": ("ai-infra", ["lambda", "lambdalabs"]),
    "CoreWeave": ("ai-infra", ["coreweave"]),
    "Crusoe": ("ai-infra", ["crusoe", "crusoeenergy"]),
    "Cerebras": ("ai-infra", ["cerebras", "cerebrassystems"]),
    "Groq": ("ai-infra", ["groq"]),
    "SambaNova": ("ai-infra", ["sambanova", "sambanovasystems"]),
    "Databricks": ("ai-infra", ["databricks"]),
    "Snowflake": ("ai-infra", ["snowflake"]),
    "Hugging Face": ("ai-infra", ["huggingface"]),
    "Datadog": ("ai-infra", ["datadog"]),
    "MongoDB": ("ai-infra", ["mongodb"]),
    "Cloudflare": ("ai-infra", ["cloudflare"]),
    "Elastic": ("ai-infra", ["elastic"]),
    "Lightning AI": ("ai-infra", ["lightningai", "lightning"]),
    "Nebius": ("ai-infra", ["nebius"]),
    "Runpod": ("ai-infra", ["runpod"]),
    # consumer / marketplace tech with large ML orgs (search, recsys, ads)
    "Pinterest": ("big-tech", ["pinterest"]),
    "Reddit": ("big-tech", ["reddit"]),
    "Airbnb": ("big-tech", ["airbnb"]),
    "DoorDash": ("big-tech", ["doordashusa", "doordash"]),
    "Instacart": ("big-tech", ["instacart"]),
    "Lyft": ("big-tech", ["lyft"]),
    "Spotify": ("big-tech", ["spotify"]),
    "Discord": ("big-tech", ["discord"]),
    "Roblox": ("big-tech", ["roblox"]),
    "Twitch": ("big-tech", ["twitch"]),
    "Dropbox": ("big-tech", ["dropbox"]),
    "Stripe": ("big-tech", ["stripe"]),
    "Coinbase": ("big-tech", ["coinbase"]),
    "Robinhood": ("big-tech", ["robinhood"]),
    "Affirm": ("big-tech", ["affirm"]),
    "Chime": ("big-tech", ["chime"]),
    "Plaid": ("big-tech", ["plaid"]),
    "Brex": ("big-tech", ["brex"]),
    "Block": ("big-tech", ["block", "square"]),
    "Etsy": ("big-tech", ["etsy"]),
    "Faire": ("big-tech", ["faire"]),
    "Thumbtack": ("big-tech", ["thumbtack"]),
    "Zillow": ("big-tech", ["zillow"]),
    "Yelp": ("big-tech", ["yelp"]),
    "Nextdoor": ("big-tech", ["nextdoor"]),
    "Quora / Poe": ("big-tech", ["quora"]),
    "Whatnot": ("big-tech", ["whatnot"]),
    "Hinge / Match": ("big-tech", ["hinge", "matchgroup"]),
    "Bumble": ("big-tech", ["bumble", "bumbleinc"]),
    "Opendoor": ("big-tech", ["opendoor"]),
    "Wayfair": ("big-tech", ["wayfair"]),
    "Grubhub": ("big-tech", ["grubhub"]),
    "Peloton": ("big-tech", ["peloton"]),
    "Asana": ("big-tech", ["asana"]),
    "Atlassian": ("big-tech", ["atlassian"]),
    "Twilio": ("big-tech", ["twilio"]),
    "Okta": ("big-tech", ["okta"]),
    "HubSpot": ("big-tech", ["hubspot", "hubspotjobs"]),
    "Gusto": ("big-tech", ["gusto"]),
    "Samsara": ("big-tech", ["samsara"]),
    "Verkada": ("big-tech", ["verkada"]),
    "Palantir": ("big-tech", ["palantir"]),
    "Upstart": ("big-tech", ["upstart"]),
    "SoFi": ("big-tech", ["sofi"]),
    "Flexport": ("big-tech", ["flexport"]),
    "Gong": ("big-tech", ["gong", "gongio"]),
    "Zoom": ("big-tech", ["zoom"]),
    "Udemy": ("big-tech", ["udemy"]),
    "Coursera": ("big-tech", ["coursera"]),
    "Checkr": ("big-tech", ["checkr"]),
    "Sift": ("big-tech", ["sift", "siftscience"]),
    "Abnormal": ("big-tech", ["abnormalsecurity", "abnormal"]),
    "Tubi": ("big-tech", ["tubi", "tubitv"]),
    "Riot Games": ("big-tech", ["riotgames"]),
    "Unity": ("big-tech", ["unity", "unity3d"]),
    "Carta": ("big-tech", ["carta"]),
    "Webflow": ("big-tech", ["webflow"]),
    "Airtable": ("big-tech", ["airtable"]),
    "Zapier": ("big-tech", ["zapier"]),
    "GitLab": ("big-tech", ["gitlab"]),
    "Mozilla": ("big-tech", ["mozilla"]),
    "Wikimedia": ("big-tech", ["wikimedia"]),
    "Khan Academy": ("big-tech", ["khanacademy"]),
    "Apollo.io": ("big-tech", ["apollo", "apolloio"]),
    "Attentive": ("big-tech", ["attentive"]),
    "Klaviyo": ("big-tech", ["klaviyo"]),
    "Toast": ("big-tech", ["toast"]),
    "Oscar Health": ("big-tech", ["oscar", "oscarhealth"]),
    "Tempus": ("big-tech", ["tempus"]),
    # robotics / autonomy / embodied
    "Waymo": ("robotics", ["waymo"]),
    "Zoox": ("robotics", ["zoox"]),
    "Nuro": ("robotics", ["nuro"]),
    "Aurora": ("robotics", ["aurora", "aurorainnovation"]),
    "Wayve": ("robotics", ["wayve"]),
    "Applied Intuition": ("robotics", ["appliedintuition"]),
    "Figure": ("robotics", ["figureai", "figure"]),
    "Physical Intelligence": ("robotics", ["physicalintelligence", "pi"]),
    "Skild AI": ("robotics", ["skildai", "skild"]),
    "1X": ("robotics", ["1x", "1xtechnologies"]),
    "Anduril": ("robotics", ["andurilindustries", "anduril"]),
    "Shield AI": ("robotics", ["shieldai"]),
    "Skydio": ("robotics", ["skydio"]),
    "Covariant": ("robotics", ["covariant"]),
    "Dexterity": ("robotics", ["dexterity"]),
    "Agility Robotics": ("robotics", ["agilityrobotics"]),
    "Boston Dynamics": ("robotics", ["bostondynamics"]),
    "Helsing": ("robotics", ["helsing"]),
    "Waabi": ("robotics", ["waabi"]),
    "Neuralink": ("robotics", ["neuralink"]),
    "Hume / Generalist": ("robotics", ["generalist", "generalistai"]),
    "Tesla Optimus-like: Mimic": ("robotics", ["mimic"]),
    # quant / finance
    "Hudson River Trading": ("quant", ["hudsonrivertrading", "wehrtyou"]),
    "Two Sigma": ("quant", ["twosigma"]),
    "Jump Trading": ("quant", ["jumptrading"]),
    "Citadel": ("quant", ["citadel"]),
    "DRW": ("quant", ["drweng", "drw"]),
    "Point72 / Cubist": ("quant", ["point72"]),
    "Jane Street": ("quant", ["janestreet"]),
    "Radix": ("quant", ["radixtrading", "radix"]),
    "Voleon": ("quant", ["voleon", "thevoleongroup"]),
    "Bridgewater": ("quant", ["bridgewater", "bridgewaterassociates"]),
}

TITLE_INCLUDE = [
    ("research-engineer", re.compile(r"\bresearch engineer", re.I)),
    ("mle", re.compile(
        r"(\b(machine[- ]learning|deep learning|ml|mle|ml/ai|ai/ml|aiml)\b.*\b(engineer|swe|developer)|"
        r"\b(engineer|swe|developer)\w*\b.*\b(machine[- ]learning|deep learning|ml|ai/ml|ml/ai)\b|\bmle\b)", re.I)),
    ("ai-engineer", re.compile(
        r"(\b(ai|a\.i\.|gen ?ai|generative ai|llm|llms|agent|agents|agentic|applied ai|nlp)\b.*\bengineer|"
        r"\bengineer\w*\b.*\b(ai|gen ?ai|generative ai|llm|llms|agents?|agentic|nlp)\b)", re.I)),
    ("mts", re.compile(
        r"member of technical staff.*\b(ml|machine learning|ai|research|model|training|inference|"
        r"applied|llm|agents?|evaluation|evals?|rl|reinforcement|multimodal|search|retrieval|recommend)", re.I)),
]
TITLE_EXCLUDE = re.compile(
    r"\b(intern|internship|new grad|university|graduate|apprentice|student|phd (intern|resid)|fellow(ship)?|"
    r"manager|director|head of|vp|vice president|chief|recruit\w*|sourcer|"
    r"sales|solutions?|customer|support|success|partner\w*|account|marketing|"
    r"field|pre-?sales|evangel\w*|advocate|relations|technical writer|program manager|product manager|"
    r"designer|counsel|legal|policy|finance|accountant|analyst|"
    r"hardware|mechanical|electrical|firmware|rf|asic|fpga|silicon|facilities|data ?cent(er|re)|network engineer|"
    r"security engineer|it engineer|qa engineer|test engineer|technician|operator|"
    r"junior|jr\.?|entry[- ]level|associate|early career)\b", re.I)


def get(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError, OSError):
        return None


TAG = re.compile(r"<[^>]+>")


def html_to_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"(?i)<\s*(br|/p|/div|/h[1-6]|/li|/ul|/ol|/tr)\s*/?>", "\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", s)
    s = TAG.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def classify(title: str):
    if TITLE_EXCLUDE.search(title):
        return None
    for track, rx in TITLE_INCLUDE:
        if rx.search(title):
            return track
    return None


def level(title: str) -> str:
    t = title.lower()
    if re.search(r"\b(principal|distinguished|fellow|senior staff|sr\.? staff)\b", t):
        return "principal"
    if re.search(r"\b(staff|lead|tech lead|tl)\b", t):
        return "staff"
    if re.search(r"\b(senior|sr\.?|iii|iv|l5|l6|ic5|ic4|e5)\b", t):
        return "senior"
    if re.search(r"\b(ii|l4|e4|mid)\b", t):
        return "mid"
    return "unlevelled"


def from_greenhouse(slug):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if not d or "jobs" not in d:
        return None
    out = []
    for j in d["jobs"]:
        out.append(dict(
            title=j.get("title", "").strip(),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            posted=(j.get("first_published") or j.get("updated_at") or "")[:10],
            updated=(j.get("updated_at") or "")[:10],
            team=", ".join(x.get("name", "") for x in (j.get("departments") or [])),
            text=html_to_text(j.get("content", "")),
        ))
    return out


def from_ashby(slug):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", timeout=90)
    if not d or "jobs" not in d:
        return None
    out = []
    for j in d["jobs"]:
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(dict(
            title=j.get("title", "").strip(),
            location=j.get("location", "") or "",
            url=j.get("jobUrl", ""),
            posted=(j.get("publishedAt") or "")[:10],
            updated=(j.get("publishedAt") or "")[:10],
            team=" / ".join(x for x in [j.get("department"), j.get("team")] if x),
            comp=comp,
            text=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
        ))
    return out


def from_lever(slug):
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(d, list):
        return None
    out = []
    for j in d:
        parts = [j.get("descriptionPlain", "")]
        for l in j.get("lists") or []:
            parts.append(l.get("text", ""))
            parts.append(html_to_text(l.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        cat = j.get("categories") or {}
        ts = j.get("createdAt")
        posted = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).strftime("%Y-%m-%d") if ts else ""
        out.append(dict(
            title=j.get("text", "").strip(),
            location=cat.get("location", "") or "",
            url=j.get("hostedUrl", ""),
            posted=posted, updated=posted,
            team=cat.get("team", "") or "",
            text="\n".join(p for p in parts if p),
        ))
    return out


SYSTEMS = [("greenhouse", from_greenhouse), ("ashby", from_ashby), ("lever", from_lever)]


def fetch_company(name: str, group: str, slugs: list[str]):
    best = None
    for slug in slugs:
        for sysname, fn in SYSTEMS:
            jobs = fn(slug)
            if jobs:
                if best is None or len(jobs) > len(best[2]):
                    best = (sysname, slug, jobs)
        if best:
            break
    if not best:
        return name, None, []
    sysname, slug, jobs = best
    kept = []
    for j in jobs:
        track = classify(j["title"])
        if not track:
            continue
        j.update(company=name, group=group, source=f"{sysname}:{slug}", track=track, level=level(j["title"]))
        kept.append(j)
    return name, (sysname, slug, len(jobs)), kept


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    allj, boards, missing = [], {}, []
    with cf.ThreadPoolExecutor(16) as ex:
        futs = [ex.submit(fetch_company, n, g, s) for n, (g, s) in COMPANIES.items()]
        for f in cf.as_completed(futs):
            name, board, kept = f.result()
            if board is None:
                missing.append(name)
                continue
            boards[name] = dict(system=board[0], slug=board[1], total_open=board[2], matched=len(kept))
            allj.extend(kept)
            print(f"{name:28s} {board[0]:10s} {board[1]:22s} open={board[2]:5d} matched={len(kept)}", flush=True)
    meta = dict(fetched=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), boards=boards, missing=sorted(missing))
    (RAW / "jobs_ats.json").write_text(json.dumps(dict(meta=meta, jobs=allj), ensure_ascii=False))
    print(f"\n{len(allj)} postings from {len(boards)} boards; no public board found for {len(missing)}: {', '.join(sorted(missing))}", file=sys.stderr)


if __name__ == "__main__":
    main()
