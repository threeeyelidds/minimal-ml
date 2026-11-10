"""Fetch open MLE / AI engineer / research engineer postings from employers
that run their own career sites (not the Greenhouse / Ashby / Lever boards that
fetch_jobs.py covers).

Only public, unauthenticated endpoints are read: the JSON search APIs behind
the career sites (Amazon, Microsoft and Netflix on Eightfold, Workday tenants,
Oracle HCM, SmartRecruiters, Workable, TikTok, Atlassian, Rippling) and the job
data embedded in the HTML of the Google and Apple search pages. Sites that
answer a plain request with a bot challenge or a login wall are recorded under
meta.failed and skipped.

Output: data/raw/jobs_bigtech.json, same record schema as jobs_ats.json.

    python tools/fetch_jobs_bigtech.py                               # everything
    python tools/fetch_jobs_bigtech.py --update Microsoft Apple      # refetch these sources, keep the rest of the file
    python tools/fetch_jobs_bigtech.py --out=/tmp/x.json Amazon      # only these sources, written elsewhere (debugging)
"""

from __future__ import annotations

import collections
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_jobs import COMPANIES, classify, from_ashby, html_to_text, level  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = "Mozilla/5.0 (compatible; job-board-reader/1.0)"
CAP = 150            # matched postings kept per company
PER_HOST = 6         # concurrent requests per host (per cluster for Workday)
QUERIES = ["machine learning engineer", "ai engineer", "research engineer", "applied ai engineer"]

quote = urllib.parse.quote


# ---------------------------------------------------------------- http

# Hosts that rate-limit (HTTP 429) at the default pace: (concurrent requests, pause after each request in s).
SLOW_HOSTS = {"apply.careers.microsoft.com": (1, 1.5), "explore.jobs.netflix.net": (2, 0.3)}

_host_sem: dict[str, threading.Semaphore] = {}
_sem_lock = threading.Lock()
HTTP_ERRORS: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)  # host -> {status: n}


def _curl(url, body, headers, timeout):
    """Fallback for hosts whose certificate chain only the system trust store
    can verify (e.g. behind a corporate TLS gateway). curl still verifies."""
    exe = shutil.which("curl")
    if not exe:
        return 0, "tls verify failed and curl not available"
    cmd = [exe, "-sS", "-m", str(timeout), "-w", "\n%{http_code}"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["--data-binary", "@-"]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, input=body, capture_output=True, timeout=timeout + 10)
    except (subprocess.TimeoutExpired, OSError) as e:
        return 0, f"curl: {type(e).__name__}"
    out = p.stdout.decode("utf-8", "replace")
    text, _, code = out.rpartition("\n")
    return (int(code) if code.isdigit() else 0), (text if code.isdigit() else p.stderr.decode("utf-8", "replace")[:200])


def http(url: str, data=None, headers: dict | None = None, timeout: int = 40):
    """-> (status, text). status 0 means a network error (text holds the reason).
    One retry on 429 / 5xx / timeouts, after a pause. Requests that still
    fail are counted in HTTP_ERRORS (written to meta.http_errors)."""
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    host = urllib.parse.urlsplit(url).netloc
    if host.endswith(".myworkdayjobs.com"):      # tenants share a cluster (wd1, wd5, ...): throttle per cluster
        host = host.split(".", 1)[1]
    limit, pause = SLOW_HOSTS.get(host, (PER_HOST, 0))
    with _sem_lock:
        sem = _host_sem.setdefault(host, threading.Semaphore(limit))
    last = (0, "no attempt")
    for attempt in range(2):
        wait = 3
        with sem:
            try:
                req = urllib.request.Request(url, data=body, headers=h)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last = (e.code, e.read()[:2000].decode("utf-8", "replace"))
                if e.code not in (429, 500, 502, 503, 504):
                    break
                if e.code == 429:        # back off while holding the host slot, so the whole host slows down
                    ra = e.headers.get("Retry-After") or ""
                    wait = min(int(ra), 90) if ra.isdigit() else 30
                    if attempt == 0:
                        time.sleep(wait)
                        wait = 0
            except urllib.error.URLError as e:
                if isinstance(e.reason, ssl.SSLCertVerificationError):
                    last = _curl(url, body, h, timeout)
                    if last[0] == 200:
                        return last
                    break
                last = (0, f"{type(e.reason).__name__}: {e.reason}"[:200])
            except (TimeoutError, ConnectionError, OSError) as e:
                last = (0, f"{type(e).__name__}: {e}"[:200])
            finally:
                if pause:
                    time.sleep(pause)
        if attempt == 0:
            time.sleep(wait)
    HTTP_ERRORS[host][str(last[0])] += 1
    return last


def jget(url, data=None, headers=None, timeout=40):
    s, t = http(url, data, headers, timeout)
    if s != 200:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- helpers

US_RX = re.compile(
    r"\b(US|USA|U\.S\.|United States|California|Washington|New York|Texas|Massachusetts|Seattle|"
    r"San Francisco|Mountain View|Sunnyvale|Santa Clara|San Jose|Palo Alto|Menlo Park|Cupertino|Redmond|"
    r"Bellevue|Austin|Boston|Los Angeles|Los Gatos|Chicago|Atlanta)\b|, [A-Z]{2}(\b|$)")
LEVEL_RANK = {"principal": 0, "staff": 0, "senior": 0, "unlevelled": 1, "mid": 2}


# classify() lets these through ("Internships", "Werkstudent", ...); same intent as its own exclusions.
EXTRA_EXCLUDE = re.compile(
    r"\b(internships?|interns|assoc\.?|co-?op|working student|werkstudent\w*|praktik\w*|stagiaire|alternance|"
    r"thesis|abschlussarbeit|masterarbeit|trainee|postdoc\w*|post-?doctoral|post doc)\b", re.I)


def match(title: str):
    """classify() from fetch_jobs.py plus the extra early-career exclusions."""
    return None if EXTRA_EXCLUDE.search(title or "") else classify(title or "")


def is_us(loc: str) -> bool:
    return bool(US_RX.search(loc or ""))


def pick(cands: list[dict], cap: int = CAP) -> list[dict]:
    """Keep title-matched candidates, dedupe by 'key', prefer US and senior
    titles when over the cap (stable, so the site's relevance order breaks ties)."""
    seen, out = set(), []
    for c in cands:
        if c["key"] in seen or not match(c["title"]):
            continue
        seen.add(c["key"])
        out.append(c)
    out.sort(key=lambda c: (not is_us(c.get("location", "")), LEVEL_RANK[level(c["title"])]))
    return out[:cap]


def paged(fetch_page, page_size: int, max_pages: int, title_of, patience: int = 2) -> list:
    """Walk a relevance-sorted result list; stop at a short page, or after
    `patience` consecutive pages without a single title match."""
    out, misses = [], 0
    for page in range(max_pages):
        items = fetch_page(page)
        if not items:
            break
        out.extend(items)
        misses = 0 if any(match(title_of(i)) for i in items) else misses + 1
        if misses >= patience or len(items) < page_size:
            break
    return out


def pmap(fn, items, workers: int = 4) -> list:
    if not items:
        return []
    with cf.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def epoch_date(ts) -> str:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    if ts > 1e12:
        ts /= 1000
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d") if ts > 0 else ""


def sections(*parts) -> str:
    """parts: (heading, html-or-text). Empty parts are dropped."""
    out = []
    for head, body in parts:
        body = html_to_text(body or "")
        if body:
            out.append(f"{head}\n{body}" if head else body)
    return "\n\n".join(out)


def rec(title, company, group, location, url, posted, team, text, source):
    title = (title or "").strip()
    track = match(title)
    if not track or not url:
        return None
    return dict(title=title, company=company, group=group, location=(location or "").strip(), url=url,
                posted=(posted or "")[:10], team=(team or "").strip(), text=(text or "").strip(), source=source,
                track=track, level=level(title))


def group_of(company: str, default: str = "big-tech") -> str:
    return COMPANIES[company][0] if company in COMPANIES else default


# ---------------------------------------------------------------- Amazon

def src_amazon():
    base = "https://www.amazon.jobs/en/search.json?sort=relevant&result_limit=100"
    queries = QUERIES + ["ml engineer", "generative ai engineer", "machine learning"]

    def run(q):
        def page(p):
            d = jget(f"{base}&base_query={quote(q)}&offset={p * 100}")
            return (d or {}).get("jobs") or []
        return paged(page, 100, 8, lambda j: j.get("title"))

    jobs = [j for lst in pmap(run, queries, 3) for j in lst]
    if not jobs:
        raise SourceError("search.json returned no jobs")
    cands = [dict(key=j.get("id_icims") or j.get("job_path"), title=j.get("title") or "",
                  location=j.get("normalized_location") or j.get("location") or "", j=j)
             for j in jobs if not j.get("is_intern") and not j.get("university_job")]
    out = []
    for c in pick(cands):
        j = c["j"]
        try:
            posted = dt.datetime.strptime(" ".join((j.get("posted_date") or "").split()), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            posted = ""
        team = " / ".join(x for x in [j.get("business_category"), (j.get("team") or {}).get("label"), j.get("job_category")]
                          if x and x != "no-team-listed")
        text = sections(("", j.get("description")), ("Basic qualifications", j.get("basic_qualifications")),
                        ("Preferred qualifications", j.get("preferred_qualifications")))
        out.append(rec(j["title"], "Amazon", "big-tech", c["location"], "https://www.amazon.jobs" + j.get("job_path", ""),
                       posted, team, text, "amazon.jobs"))
    return "amazon.jobs search.json", out


# ---------------------------------------------------------------- Eightfold (Microsoft, Netflix)

def src_microsoft():
    base = "https://apply.careers.microsoft.com/api/pcsx"

    def run(q):
        def page(p):
            d = jget(f"{base}/search?domain=microsoft.com&query={quote(q)}&start={p * 10}&sort_by=relevance")
            return ((d or {}).get("data") or {}).get("positions") or []
        return paged(page, 10, 15, lambda j: j.get("name"), patience=3)

    # the host answers 429 above roughly one request every two seconds (see SLOW_HOSTS): this source takes ~6 min
    queries = QUERIES + ["ml engineer", "ai software engineer", "machine learning"]
    pos = [j for lst in pmap(run, queries, 1) for j in lst]
    if not pos:
        raise SourceError("Eightfold pcsx/search returned no positions")
    cands = [dict(key=j["id"], title=j.get("name") or "", location="; ".join(j.get("locations") or []), j=j) for j in pos]

    def detail(c):
        d = jget(f"{base}/position_details?position_id={c['key']}&domain=microsoft.com&hl=en")
        j = (d or {}).get("data") or {}
        if not j:
            return None
        team = " / ".join(x for x in [j.get("department")] + (j.get("efcustomTextTaDisciplineName") or []) if x)
        team = " / ".join(dict.fromkeys(team.split(" / ")))
        return rec(j.get("name"), "Microsoft", "big-tech", "; ".join(j.get("locations") or []),
                   j.get("publicUrl") or f"https://apply.careers.microsoft.com/careers/job/{c['key']}",
                   epoch_date(j.get("postedTs")), team, html_to_text(j.get("jobDescription") or ""), "eightfold:microsoft")

    return "Eightfold API (apply.careers.microsoft.com/api/pcsx)", pmap(detail, pick(cands), 1)


def src_netflix():
    base = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
    queries = QUERIES + ["machine learning", "research scientist engineer"]

    def run(q):
        def page(p):
            d = jget(f"{base}?domain=netflix.com&query={quote(q)}&start={p * 10}&num=10&sort_by=relevance")
            return (d or {}).get("positions") or []
        return paged(page, 10, 12, lambda j: j.get("name"), patience=3)

    pos = [j for lst in pmap(run, queries, 3) for j in lst]
    if not pos:
        raise SourceError("Eightfold apply/v2/jobs returned no positions")
    cands = [dict(key=j["id"], title=j.get("name") or "", location=j.get("location") or "", j=j) for j in pos]

    def detail(c):
        j = jget(f"{base}/{c['key']}?domain=netflix.com")
        if not j:
            return None
        return rec(j.get("name"), "Netflix", "big-tech", "; ".join(j.get("locations") or [j.get("location") or ""]),
                   j.get("canonicalPositionUrl") or f"https://explore.jobs.netflix.net/careers/job/{c['key']}",
                   epoch_date(j.get("t_create")), " / ".join(x for x in [j.get("business_unit"), j.get("department")] if x),
                   html_to_text(j.get("job_description") or ""), "eightfold:netflix")

    return "Eightfold API (explore.jobs.netflix.net)", pmap(detail, pick(cands), 5)


# ---------------------------------------------------------------- Workday

# company -> (tenant, wdN, site, group). All verified to answer the public
# cxs search endpoint. "enterprise" marks non-tech employers.
WORKDAY = {
    "Nvidia": ("nvidia", "wd5", "NVIDIAExternalCareerSite", "big-tech"),
    "Salesforce": ("salesforce", "wd12", "External_Career_Site", "big-tech"),
    "Adobe": ("adobe", "wd5", "external_experienced", "big-tech"),
    "Snap": ("snapchat", "wd1", "snap", "big-tech"),
    "Intel": ("intel", "wd1", "External", "big-tech"),
    "Zillow": ("zillow", "wd5", "Zillow_Group_External", "big-tech"),
    "PayPal": ("paypal", "wd1", "jobs", "big-tech"),
    "Workday": ("workday", "wd5", "Workday", "big-tech"),
    "Capital One": ("capitalone", "wd12", "Capital_One", "big-tech"),
    "Disney": ("disney", "wd5", "disneycareer", "big-tech"),
    "Etsy": ("etsy", "wd5", "Etsy_Careers", "big-tech"),
    "Zoom": ("zoom", "wd5", "Zoom", "big-tech"),
    "eBay": ("ebay", "wd5", "apply", "big-tech"),
    "Expedia": ("expedia", "wd108", "search", "big-tech"),
    "Cisco": ("cisco", "wd5", "Cisco_Careers", "big-tech"),
    "Samsung": ("sec", "wd3", "Samsung_Careers", "big-tech"),
    "Red Hat": ("redhat", "wd5", "jobs", "big-tech"),
    "HP": ("hp", "wd5", "ExternalCareerSite", "big-tech"),
    "Hewlett Packard Enterprise": ("hpe", "wd5", "Jobsathpe", "big-tech"),
    "Broadcom": ("broadcom", "wd1", "External_Career", "big-tech"),
    "Yahoo": ("ouryahoo", "wd5", "careers", "big-tech"),
    "CrowdStrike": ("crowdstrike", "wd5", "crowdstrikecareers", "big-tech"),
    "Cadence": ("cadence", "wd1", "External_Careers", "big-tech"),
    "Analog Devices": ("analogdevices", "wd1", "External", "big-tech"),
    "Marvell": ("marvell", "wd1", "MarvellCareers", "big-tech"),
    "Zendesk": ("zendesk", "wd1", "Zendesk", "big-tech"),
    "Genesys": ("genesys", "wd1", "Genesys", "big-tech"),
    "Sony": ("sonyglobal", "wd1", "SonyGlobalCareers", "big-tech"),
    "Warner Bros. Discovery": ("warnerbros", "wd5", "global", "big-tech"),
    "Visa": ("visa", "wd5", "Visa", "enterprise"),
    "Mastercard": ("mastercard", "wd1", "CorporateCareers", "enterprise"),
    "Target": ("target", "wd5", "targetcareers", "enterprise"),
    "Home Depot": ("homedepot", "wd5", "CareerDepot", "enterprise"),
    "Nike": ("nike", "wd1", "nke", "enterprise"),
    "GM": ("generalmotors", "wd5", "Careers_GM", "enterprise"),
    "Blue Origin": ("blueorigin", "wd5", "BlueOrigin", "enterprise"),
    "Morgan Stanley": ("ms", "wd5", "External", "enterprise"),
    "BlackRock": ("blackrock", "wd1", "BlackRock_Professional", "enterprise"),
    "Fidelity": ("fmr", "wd1", "FidelityCareers", "enterprise"),
    "State Street": ("statestreet", "wd1", "Global", "enterprise"),
    "Wells Fargo": ("wf", "wd1", "WellsFargoJobs", "enterprise"),
    "Bank of America": ("ghr", "wd1", "Lateral-US", "enterprise"),
    "GEICO": ("geico", "wd1", "External", "enterprise"),
    "S&P Global": ("spgi", "wd5", "SPGI_Careers", "enterprise"),
    "Thomson Reuters": ("thomsonreuters", "wd5", "External_Career_Site", "enterprise"),
    "RELX (LexisNexis / Elsevier)": ("relx", "wd3", "relx", "enterprise"),
    "Gartner": ("gartner", "wd5", "EXT", "enterprise"),
    "T-Mobile": ("tmobile", "wd1", "External", "enterprise"),
    "Verizon": ("verizon", "wd12", "verizon-careers", "enterprise"),
    "AT&T": ("att", "wd1", "ATTGeneral", "enterprise"),
    "CVS Health": ("cvshealth", "wd1", "CVS_Health_Careers", "enterprise"),
    "Cigna": ("cigna", "wd5", "cignacareers", "enterprise"),
    "Humana": ("humana", "wd5", "Humana_External_Career_Site", "enterprise"),
    "Pfizer": ("pfizer", "wd1", "PfizerCareers", "enterprise"),
    "GSK": ("gsk", "wd5", "GSKCareers", "enterprise"),
    "Novartis": ("novartis", "wd3", "Novartis_Careers", "enterprise"),
    "AstraZeneca": ("astrazeneca", "wd3", "Careers", "enterprise"),
    "Sanofi": ("sanofi", "wd3", "SanofiCareers", "enterprise"),
    "Merck": ("msd", "wd5", "SearchJobs", "enterprise"),
    "Johnson & Johnson": ("jj", "wd5", "JJ", "enterprise"),
    "Amgen": ("amgen", "wd1", "Careers", "enterprise"),
    "Roche / Genentech": ("roche", "wd3", "roche-ext", "enterprise"),
    "GE HealthCare": ("gehc", "wd5", "GEHC_ExternalSite", "enterprise"),
    "Philips": ("philips", "wd3", "jobs-and-careers", "enterprise"),
    "Northrop Grumman": ("ngc", "wd1", "Northrop_Grumman_External_Site", "enterprise"),
    "Boeing": ("boeing", "wd1", "EXTERNAL_CAREERS", "enterprise"),
}


def src_workday(company):
    tenant, wd, site, group = WORKDAY[company]
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    first_status = []

    def run(q):
        def page(p):
            s, t = http(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": p * 20, "searchText": q})
            first_status.append(s)
            try:
                return (json.loads(t).get("jobPostings") or []) if s == 200 else []
            except ValueError:
                return []
        return paged(page, 20, 10, lambda j: j.get("title"))

    posts = [j for lst in pmap(run, QUERIES, 2) for j in lst]
    if not posts:
        raise SourceError(f"Workday {tenant}.{wd}/{site}: no postings (HTTP {first_status[0] if first_status else '?'})")
    cands = [dict(key=j["externalPath"], title=j.get("title") or "", location=j.get("locationsText") or "", j=j)
             for j in posts if j.get("externalPath")]

    def detail(c):
        d = jget(api + c["key"])
        i = (d or {}).get("jobPostingInfo") or {}
        if not i:
            return None
        locs = [i.get("location") or ""] + list(i.get("additionalLocations") or [])
        return rec(i.get("title") or c["title"], company, group, "; ".join(x for x in locs if x),
                   i.get("externalUrl") or f"{host}/en-US/{site}{c['key']}", i.get("startDate") or "", "",
                   html_to_text(i.get("jobDescription") or ""), f"workday:{tenant}")

    return f"Workday cxs API ({tenant}.{wd}/{site})", pmap(detail, pick(cands), 4)


# ---------------------------------------------------------------- Google (+ DeepMind)

GOOGLE_RX = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);", re.S)


def _google_jobs(params: str, max_pages: int):
    base = "https://www.google.com/about/careers/applications/jobs/results/"

    def page(p):
        s, t = http(f"{base}?{params}&page={p + 1}")
        m = GOOGLE_RX.search(t) if s == 200 else None
        if not m:
            return []
        try:
            return json.loads(m.group(1))[0] or []
        except (ValueError, IndexError, TypeError):
            return []
    return paged(page, 20, max_pages, lambda j: j[1])


def _google_rec(j):
    def part(i):
        v = j[i] if len(j) > i else None
        return v[1] if isinstance(v, list) and len(v) > 1 else ""
    slug = re.sub(r"[^a-z0-9]+", "-", j[1].lower()).strip("-")
    employer = j[7] or "Google"
    company, group = ("Google DeepMind", "lab") if employer == "DeepMind" else ("Google", "big-tech")
    locs = "; ".join(x[0] for x in (j[9] or []) if x and x[0])
    text = sections(("About the job", part(10)), ("Responsibilities", part(3)), ("", part(4)))
    return rec(j[1], company, group, locs,
               f"https://www.google.com/about/careers/applications/jobs/results/{j[0]}-{slug}",
               epoch_date((j[12] or [0])[0] if len(j) > 12 else 0), employer if employer not in ("Google", "DeepMind") else "",
               text, "google.com/about/careers")


def src_google():
    qs = ['"machine learning engineer"', "machine learning software engineer", '"research engineer"',
          "AI engineer", "generative AI software engineer"]
    jobs = [j for lst in pmap(lambda q: _google_jobs("q=" + quote(q), 15), qs, 3) for j in lst]
    if not jobs:
        raise SourceError("no AF_initDataCallback ds:1 job data in the results HTML")
    cands = [dict(key=j[0], title=j[1], location="; ".join(x[0] for x in (j[9] or []) if x), j=j)
             for j in jobs if (j[7] or "Google") != "DeepMind"]
    return "embedded JSON in careers search HTML", [_google_rec(c["j"]) for c in pick(cands)]


def src_deepmind():
    jobs = _google_jobs("company=DeepMind", 12)
    jobs = [j for j in jobs if j[7] == "DeepMind"]
    if not jobs:
        raise SourceError("Google careers company=DeepMind returned no jobs (the Greenhouse board 'deepmind' is gone)")
    cands = [dict(key=j[0], title=j[1], location="; ".join(x[0] for x in (j[9] or []) if x), j=j) for j in jobs]
    return "Google careers search HTML, company=DeepMind", [_google_rec(c["j"]) for c in pick(cands)]


# ---------------------------------------------------------------- Apple

APPLE_RX = re.compile(r'window\.__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.S)


def _apple_state(url):
    s, t = http(url)
    m = APPLE_RX.search(t) if s == 200 else None
    if not m:
        return {}
    try:
        return json.loads(json.loads('"' + m.group(1) + '"')).get("loaderData") or {}
    except ValueError:
        return {}


def src_apple():
    def run(q):
        def page(p):
            st = _apple_state(f"https://jobs.apple.com/en-us/search?search={quote(q)}&sort=relevance&page={p + 1}")
            return (st.get("search") or {}).get("searchResults") or []
        return paged(page, 20, 12, lambda j: j.get("postingTitle"))

    res = [j for lst in pmap(run, QUERIES, 2) for j in lst]
    if not res:
        raise SourceError("no __staticRouterHydrationData search results in the HTML")
    cands = [dict(key=j["positionId"], title=j.get("postingTitle") or "",
                  location="; ".join(l.get("name", "") for l in j.get("locations") or []), j=j) for j in res]

    def detail(c):
        j = (_apple_state(f"https://jobs.apple.com/en-us/details/{c['key']}").get("jobDetails") or {}).get("jobsData") or {}
        if not j:
            return None
        locs = "; ".join(dict.fromkeys(l.get("name", "") for l in j.get("locations") or [])) or c["location"]
        teams = j.get("teamNames") or []
        text = sections(("", j.get("jobSummary")), ("Description", j.get("description")),
                        ("Minimum qualifications", j.get("minimumQualifications")),
                        ("Preferred qualifications", j.get("preferredQualifications")))
        return rec(j.get("postingTitle"), "Apple", "big-tech", locs,
                   f"https://jobs.apple.com/en-us/details/{j.get('jobNumber') or c['key']}",
                   j.get("postingDateMeta") or "", " / ".join(t for t in teams if isinstance(t, str)), text, "jobs.apple.com")

    return "embedded JSON in jobs.apple.com search + detail HTML", pmap(detail, pick(cands), 4)


# ---------------------------------------------------------------- TikTok / ByteDance

def src_tiktok():
    api = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
    hdr = {"website-path": "tiktok", "accept-language": "en-US"}

    def run(q):
        def page(p):
            d = jget(api, {"recruitment_id_list": [], "job_category_id_list": [], "subject_id_list": [],
                           "location_code_list": [], "keyword": q, "limit": 50, "offset": p * 50}, hdr)
            return ((d or {}).get("data") or {}).get("job_post_list") or []
        return paged(page, 50, 8, lambda j: j.get("title"))

    posts = [j for lst in pmap(run, QUERIES, 2) for j in lst]
    if not posts:
        raise SourceError("lifeattiktok search API returned no posts")

    def loc(j):
        c = j.get("city_info") or {}
        names = []
        while c:
            names.append(c.get("en_name") or "")
            c = c.get("parent")
        return ", ".join(dict.fromkeys(n for n in names if n))

    cands = [dict(key=j["id"], title=j.get("title") or "", location=loc(j), j=j) for j in posts]
    out = []
    for c in pick(cands):
        j = c["j"]
        cat = j.get("job_category") or {}
        team = " / ".join(x for x in [(cat.get("parent") or {}).get("en_name"), cat.get("en_name")] if x)
        out.append(rec(j["title"], "TikTok", "big-tech", c["location"], f"https://lifeattiktok.com/search/{j['id']}", "",
                       team, sections(("", j.get("description")), ("Qualifications", j.get("requirement"))), "lifeattiktok.com"))
    return "lifeattiktok.com search API", out


# ---------------------------------------------------------------- Ashby boards the first pass missed (slug differs)

ASHBY = {
    "Mistral AI": ("mistral.ai", "lab"),
    "Black Forest Labs": ("black-forest-labs", None),
    "Luma AI": ("lumaai", None),
    "Hippocratic AI": ("Hippocratic AI", None),
}


def src_ashby(company):
    slug, group = ASHBY[company]
    jobs = from_ashby(quote(slug))
    if not jobs:
        raise SourceError(f"Ashby board '{slug}' returned nothing")
    out = [rec(j["title"], company, group or group_of(company), j["location"], j["url"], j["posted"], j["team"], j["text"],
               f"ashby:{slug}") for j in jobs]
    return f"Ashby posting API (slug '{slug}')", [o for o in out if o][:CAP]


# ---------------------------------------------------------------- Hugging Face (Workable)

def src_huggingface():
    base = "https://apply.workable.com/api"
    jobs, token = [], None
    for _ in range(10):
        d = jget(f"{base}/v3/accounts/huggingface/jobs", {"token": token} if token else {})
        if not d:
            break
        jobs += d.get("results") or []
        token = d.get("nextPage")
        if not token:
            break
    if not jobs:
        raise SourceError("Workable account 'huggingface' returned no jobs")

    def detail(j):
        if not match(j.get("title")):
            return None
        d = jget(f"{base}/v2/accounts/huggingface/jobs/{j['shortcode']}") or {}
        l = d.get("location") or j.get("location") or {}
        loc = ", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x) + (" (remote)" if j.get("remote") else "")
        return rec(j["title"], "Hugging Face", "lab", loc, f"https://apply.workable.com/huggingface/j/{j['shortcode']}/",
                   (j.get("published") or "")[:10], ", ".join(j.get("department") or []),
                   sections(("", d.get("description")), ("Requirements", d.get("requirements"))), "workable:huggingface")

    return f"Workable API ({len(jobs)} open postings in total)", pmap(detail, jobs, 4)


# ---------------------------------------------------------------- Oracle HCM (Oracle, JPMorgan)

ORACLE_HCM = {
    "Oracle": ("eeho.fa.us2.oraclecloud.com", "CX_45001", "big-tech"),
    "JPMorgan Chase": ("jpmc.fa.oraclecloud.com", "CX_1001", "enterprise"),
}


def src_oracle_hcm(company):
    host, site, group = ORACLE_HCM[company]
    api = f"https://{host}/hcmRestApi/resources/latest"

    def run(q):
        def page(p):
            d = jget(f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations"
                     f"&finder=findReqs;siteNumber={site},keyword=%22{quote(q)}%22,limit=25,offset={p * 25},sortBy=RELEVANCY")
            items = (d or {}).get("items") or [{}]
            return items[0].get("requisitionList") or []
        return paged(page, 25, 10, lambda j: j.get("Title"))

    reqs = [j for lst in pmap(run, QUERIES, 2) for j in lst]
    if not reqs:
        raise SourceError(f"Oracle HCM {host} returned no requisitions")
    cands = [dict(key=j["Id"], title=j.get("Title") or "", location=j.get("PrimaryLocation") or "", j=j) for j in reqs]

    def detail(c):
        d = jget(f"{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22{c['key']}%22,siteNumber={site}")
        items = (d or {}).get("items") or []
        if not items:
            return None
        j = items[0]
        locs = [j.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in j.get("secondaryLocations") or []]
        text = sections(("", j.get("ExternalDescriptionStr")), ("Responsibilities", j.get("ExternalResponsibilitiesStr")),
                        ("Qualifications", j.get("ExternalQualificationsStr")))
        return rec(j.get("Title"), company, group, "; ".join(x for x in locs if x),
                   f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{c['key']}",
                   (j.get("ExternalPostedStartDate") or "")[:10], " / ".join(x for x in [j.get("Category"), j.get("JobFunction")] if x),
                   text, f"oraclehcm:{host.split('.')[0]}")

    return f"Oracle HCM recruiting API ({host}, {site})", pmap(detail, pick(cands), 4)


# ---------------------------------------------------------------- SmartRecruiters

SMARTRECRUITERS = {
    "ServiceNow": ("ServiceNow", "big-tech"),
    "LinkedIn": ("LinkedIn3", "big-tech"),
    "Canva": ("Canva", None),
    "Bosch": ("BoschGroup", "enterprise"),
}


def src_smartrecruiters(company):
    ident, group = SMARTRECRUITERS[company]
    api = f"https://api.smartrecruiters.com/v1/companies/{ident}/postings"

    def run(q):
        def page(p):
            return (jget(f"{api}?q={quote(q)}&limit=100&offset={p * 100}") or {}).get("content") or []
        return paged(page, 100, 5, lambda j: j.get("name"))

    posts = [j for lst in pmap(run, QUERIES, 2) for j in lst]
    if not posts:
        raise SourceError(f"SmartRecruiters company '{ident}' returned no postings")

    def loc(j):
        l = j.get("location") or {}
        return l.get("fullLocation") or ", ".join(x for x in [l.get("city"), l.get("region"), (l.get("country") or "").upper()] if x)

    cands = [dict(key=j["id"], title=j.get("name") or "", location=loc(j), j=j) for j in posts]

    def detail(c):
        j = jget(f"{api}/{c['key']}")
        if not j:
            return None
        s = (j.get("jobAd") or {}).get("sections") or {}
        text = sections(*[((s.get(k) or {}).get("title") or "", (s.get(k) or {}).get("text"))
                          for k in ("jobDescription", "qualifications", "additionalInformation")])
        return rec(j.get("name"), company, group or group_of(company), loc(j) or c["location"],
                   j.get("postingUrl") or f"https://jobs.smartrecruiters.com/{ident}/{c['key']}",
                   (j.get("releasedDate") or "")[:10], (j.get("department") or {}).get("label") or "", text,
                   f"smartrecruiters:{ident}")

    return f"SmartRecruiters posting API ({ident})", pmap(detail, pick(cands), 4)


# ---------------------------------------------------------------- Atlassian, Rippling

def src_atlassian():
    d = jget("https://www.atlassian.com/endpoint/careers/listings", timeout=90)
    if not isinstance(d, list) or not d:
        raise SourceError("atlassian.com/endpoint/careers/listings returned nothing")
    out = []
    for j in d:
        text = sections(("", j.get("overview")), ("Responsibilities", j.get("responsibilities")),
                        ("Qualifications", j.get("qualifications")))
        out.append(rec(j.get("title"), "Atlassian", group_of("Atlassian"), "; ".join(j.get("locations") or []),
                       f"https://www.atlassian.com/company/careers/details/{j.get('id')}", "", j.get("category") or "",
                       text, "atlassian.com"))
    return f"atlassian.com careers listings endpoint ({len(d)} open postings in total)", [o for o in out if o][:CAP]


def src_rippling():
    api = "https://api.rippling.com/platform/api/ats/v1/board/rippling/jobs"
    d = jget(api, timeout=90)
    if not isinstance(d, list) or not d:
        raise SourceError("Rippling ATS board API returned nothing")
    # one uuid per location for the same role; keep the first
    cands, seen = [], set()
    for j in d:
        t = (j.get("name") or "").strip()
        if t in seen:
            continue
        seen.add(t)
        cands.append(dict(key=j["uuid"], title=t, location=(j.get("workLocation") or {}).get("label") or "", j=j))

    def detail(c):
        j = jget(f"{api}/{c['key']}")
        if not j:
            return None
        desc = j.get("description") or {}
        text = sections(*[("", v) for k, v in desc.items() if k != "company" and isinstance(v, str)]) if isinstance(desc, dict) \
            else html_to_text(str(desc))
        return rec(j.get("name"), "Rippling", group_of("Rippling"), "; ".join(j.get("workLocations") or []) or c["location"],
                   j.get("url") or c["j"].get("url"), (j.get("createdOn") or "")[:10], (j.get("department") or {}).get("label")
                   or (j.get("department") or {}).get("name") or "", text, "rippling-ats")

    return f"Rippling ATS board API ({len(d)} open postings in total)", pmap(detail, pick(cands), 4)


# ---------------------------------------------------------------- sources that only get a liveness probe

def src_uber():
    s, t = http("https://www.uber.com/api/loadSearchJobsResults?localeCode=en-US", {"params": {"query": "machine learning", "limit": 50, "page": 0}},
                {"x-csrf-token": "x"})
    s2, t2 = http("https://jobs.uber.com/en/jobs/?query=machine%20learning")
    why = "Cloudflare browser challenge" if "Just a moment" in t2 or "challenge" in t2 else t2[:80]
    raise SourceError(f"old RPC www.uber.com/api/loadSearchJobsResults is gone (HTTP {s}: {t[:90].strip()}); "
                      f"careers moved to jobs.uber.com, which answers HTTP {s2} ({why}); needs a real browser")


def src_meta():
    s, t = http("https://www.metacareers.com/jobs?q=machine%20learning%20engineer")
    n = len(re.findall(r"job_details/\d+", t))
    raise SourceError(f"metacareers.com search page (HTTP {s}) carries no job data ({n} job links): results come from a GraphQL "
                      "call that needs session tokens from a browser-rendered page. Not pursued further: robots.txt states that "
                      "automated collection needs Meta's written permission. (The sitemap /jobsearch/sitemap.xml lists ~1,300 "
                      "job_details URLs without titles; each page carries a schema.org JobPosting block.)")


# Not fetched; reasons from the probes made when this script was written.
NOT_RESOLVED = {
    "Walmart": "Workday walmart.wd5 (sites WalmartExternal, External) answers HTTP 422",
    "Qualcomm": "Workday qualcomm.wd12/External returns 0 results for every query; qualcomm.wd5 answers HTTP 422",
    "Dell": "Workday dell.wd1 (sites External, ExternalNonPublic) answers HTTP 422",
    "Yelp": "Workday yelp.wd5 answers HTTP 401; no Greenhouse / Ashby / Lever board under yelp, yelpinc",
    "Wayfair": "Workday guesses answer HTTP 401 / 422; SmartRecruiters 'Wayfair' is empty",
    "Unity": "Workday unity.wd1 / unity3d.wd1 answer HTTP 422",
    "Intuit": "Workday intuit.wd1 answers HTTP 401; own TalentBrew site not parsed (optional)",
    "IBM": "search API returns ids only and detail pages are rendered HTML; not parsed (optional)",
    "Tesla": "not attempted beyond one Workday guess (HTTP 422); own site (optional)",
    "LinkedIn (linkedin.com/jobs)": "login-walled, skipped as instructed (LinkedIn's own postings come from SmartRecruiters)",
    "Two Sigma": "no board on Greenhouse / Ashby / Lever / SmartRecruiters / Workday under the slugs tried",
    "Citadel": "no board on Greenhouse / Workday under the slugs tried",
    "Grammarly": "no board on Greenhouse (grammarly, superhuman) / SmartRecruiters / Workday",
    "Applied Intuition": "no board on Greenhouse / Ashby under the slugs tried",
    "Aurora": "no board on Greenhouse / Workday under the slugs tried",
    "Skild AI": "no Ashby board under skild-ai",
    "Replicate": "no Ashby board under replicate, replicate.com",
    "Groq": "no board on Ashby (groq.com) / Greenhouse (groqinc) / Workable",
    "Hebbia": "no board on Ashby (hebbia.ai) / Greenhouse",
    "Safe Superintelligence": "no public job board found",
}


# ---------------------------------------------------------------- main

def build_sources():
    S = collections.OrderedDict()
    S["Amazon"] = src_amazon
    S["Microsoft"] = src_microsoft
    S["Nvidia"] = lambda: src_workday("Nvidia")
    S["Netflix"] = src_netflix
    S["Uber"] = src_uber
    S["Google DeepMind"] = src_deepmind
    S["Mistral AI"] = lambda: src_ashby("Mistral AI")
    S["Google"] = src_google
    S["Meta"] = src_meta
    S["Apple"] = src_apple
    S["Hugging Face"] = src_huggingface
    S["TikTok"] = src_tiktok
    for c in WORKDAY:
        if c != "Nvidia":
            S[c] = (lambda c=c: src_workday(c))
    for c in ORACLE_HCM:
        S[c] = (lambda c=c: src_oracle_hcm(c))
    for c in SMARTRECRUITERS:
        S[c] = (lambda c=c: src_smartrecruiters(c))
    S["Atlassian"] = src_atlassian
    S["Rippling"] = src_rippling
    for c in ASHBY:
        if c != "Mistral AI":
            S[c] = (lambda c=c: src_ashby(c))
    return S


def run_source(name, fn):
    t0 = time.time()
    try:
        method, recs = fn()
        recs = [r for r in recs if r]
        return name, method, recs, None, time.time() - t0
    except SourceError as e:
        return name, None, [], str(e), time.time() - t0
    except Exception as e:  # a parser broke: record it, keep the other sources
        return name, None, [], f"{type(e).__name__}: {e}"[:300], time.time() - t0


def main(argv):
    RAW.mkdir(parents=True, exist_ok=True)
    S = build_sources()
    order = {n: i for i, n in enumerate(S)}
    only = [a for a in argv if not a.startswith("-")]
    outs = [a.split("=", 1)[1] for a in argv if a.startswith("--out=")]
    update = "--update" in argv
    out = pathlib.Path(outs[0]) if outs else RAW / "jobs_bigtech.json"
    unknown = [n for n in only if n not in S]
    if unknown:
        sys.exit(f"unknown source(s): {', '.join(unknown)}")
    if only and not (outs or update):
        sys.exit("a partial run needs --update (merge into the existing output) or --out=PATH")
    if only:
        S = collections.OrderedDict((k, v) for k, v in S.items() if k in only)

    jobs, sources, failed, http_errors = [], {}, {}, {}
    if update:  # keep everything the named sources do not own
        old = json.loads(out.read_text())
        jobs = [j for j in old["jobs"] if j["company"] not in S]
        sources = {k: v for k, v in old["meta"]["sources"].items() if k not in S}
        failed = {k: v for k, v in old["meta"]["failed"].items() if k not in S}
        http_errors = old["meta"].get("http_errors", {})
    seen = {j["url"] for j in jobs}

    with cf.ThreadPoolExecutor(12) as ex:
        futs = [ex.submit(run_source, n, f) for n, f in S.items()]
        for f in cf.as_completed(futs):
            name, method, recs, err, secs = f.result()
            kept = []
            for r in recs:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                kept.append(r)
            kept = kept[:CAP]
            if err or not kept:
                failed[name] = err or "source answered but no posting title matched the filters"
                print(f"{name:30s} FAILED  {failed[name][:110]}  ({secs:.0f}s)", flush=True)
                continue
            sources[name] = dict(method=method, matched=len(kept))
            jobs.extend(kept)
            short = sum(len(r["text"]) < 800 for r in kept)
            print(f"{name:30s} matched={len(kept):4d} short_text={short:3d}  {method}  ({secs:.0f}s)", flush=True)

    if not only:
        for k, v in NOT_RESOLVED.items():
            failed.setdefault(k, v)
    # requests that failed after the retry: a non-empty entry means that host's source may be incomplete
    http_errors.update({h: dict(c) for h, c in HTTP_ERRORS.items()})
    jobs.sort(key=lambda r: (order.get(r["company"], 999), r["title"]))
    meta = dict(fetched=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                sources=dict(sorted(sources.items(), key=lambda kv: order.get(kv[0], 999))), failed=failed,
                http_errors=http_errors)
    out.write_text(json.dumps(dict(meta=meta, jobs=jobs), ensure_ascii=False))
    print(f"\n{len(jobs)} postings from {len(sources)} employers -> {out}; {len(failed)} failed / not resolved; "
          f"requests that failed after a retry: {dict((h, dict(c)) for h, c in HTTP_ERRORS.items()) or 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
