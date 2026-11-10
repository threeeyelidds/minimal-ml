# Question bank

One YAML file per skill area. `tools/build_site.py` turns these files into the
skill pages (`skills/<area>.qmd`) and the review app (`review/index.qmd`), so a
card is written once and appears in both.

The areas and skill ids come from `tools/taxonomy.py`, which is also what the
job-posting analysis counts. Every skill id in an area needs at least one card
and one checklist entry, so the bank covers every skill the postings ask for.
`python tools/validate_bank.py` checks that.

## File format

```yaml
domain: llmapp                 # area id from tools/taxonomy.py, or "cases"
title: LLM application engineering
summary: >-                    # 2-4 sentences: what the area is, why postings ask for it
  ...
senior_bar:                    # 4-7 bullets: what separates a senior answer from a mid-level one
  - ...
rounds:                        # interview rounds where the area is tested
  - ML system design
notes:                         # deep-dive notes on this site, paths from the repo root
  - notes/applied_ml/harness.qmd
checklist:                     # per skill: what you must be able to explain without notes
  - skill: rag
    items:
      - Chunking strategies and how chunk size trades recall against precision
cards:
  - id: llmapp-001             # <domain>-NNN, unique
    skill: rag                 # primary skill id (must belong to this area; any id for "cases")
    also: [vector-db]          # optional: other skill ids the card exercises (any area)
    type: concept              # concept | tradeoff | design | debug | coding | derivation | behavioral | case
    level: core                # core (must know) | senior (expected at senior) | staff (stretch)
    tracks: [mle, ai, re]      # mle = ML engineer, ai = AI engineer, re = research engineer
    q: >-
      The question, phrased as an interviewer would ask it.
    a: |
      The answer in Markdown.
    probes:                    # optional: 1-3 follow-ups an interviewer would push on
      - ...
```

## Writing cards

- Ask what a senior-level interviewer asks. Prefer "why", "when does it break",
  "how would you debug", "what would you measure" and "design X" over
  definitions. Include the definition-level cards that the rest depend on, at
  level `core`.
- The answer is what a strong candidate says: the direct answer first, then the
  reasoning, the trade-off, and a concrete number or example where one exists.
  Usually 80 to 220 words; `case` and `design` cards can run to 400.
- Be exact. Formulas in LaTeX (`$...$` inline, `$$...$$` display), complexity,
  shapes and units where they matter. If a fact is contested or version
  specific, say so. Do not invent benchmark numbers or paper results.
- Markdown allowed in answers: paragraphs, bullet and numbered lists, bold,
  inline code, fenced code blocks (with a language), tables, links. No headings
  (`#`) and no fenced divs (`:::`), because the answer is embedded in a page.
- `coding` cards: give a compact, correct reference implementation (Python /
  PyTorch / NumPy unless the skill is another language), plus the pitfalls an
  interviewer checks.
- `behavioral` cards: the answer is guidance: what the interviewer is testing,
  the structure of a strong answer, and the mistakes that sink it. No invented
  personal stories.
- Quote YAML carefully: use `>-` for questions and `|` for answers; inside `|`
  blocks LaTeX backslashes need no escaping.

## Questions reported from interviews

`reported.yml` records questions that third-party sites list as asked in interviews. It is not a card
file. Each entry has the employer(s), the role and month as the source gives them, the source and the
path on it, a paraphrased topic, and `cards`: the ids of the cards that cover the topic (empty when there
is none yet).

```yaml
reports:
  - company: [OpenAI]
    role: Machine Learning Engineer
    date: 2026-09            # YYYY-MM as listed by the source, or '' when undated
    source: prachub          # key in `sources`
    path: /interview-questions/...
    topic: Paraphrase of what the question is about
    cards: [dl-019]
```

`tools/build_site.py` uses it to tag matched cards ("asked at ..."), to list the sources under each
answer, to give the review app its "Asked in interviews" filter and mock round, and to build the table
in the report. `tools/validate_bank.py` checks the fields and that every card id exists.

Rules: paraphrase the topic and link to the source instead of copying its text; record only what the
source states; the reports are self-submitted and unverified, and the site says so wherever it shows
them. Sources that need a login or refuse automated reading are not used.
