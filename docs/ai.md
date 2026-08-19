# The AI layer

The product's credibility rests on this layer being honest about what it knows. A
competitor intelligence tool that invents a price is worse than no tool, because the user
acts on it.

---

## Structure

```
app/ai/
├── base.py                    AIProvider protocol: complete_structured() + embed()
├── service.py                 AIService — the only thing the rest of the app calls
├── schemas.py                 Pydantic models every response must satisfy
├── sanitize.py                untrusted-content fencing and injection detection
├── prompts/                   one module per stage, each with a VERSION
└── providers/
    ├── anthropic_provider.py  real provider
    ├── mock_provider.py       labelled development provider
    └── embeddings.py          Voyage, or a lexical hashing fallback
```

Nothing outside this package knows which provider is in use. `AIService` is the boundary;
swapping or adding a provider touches this directory and nothing else.

There is deliberately **no "give me text" method**. Every call site must declare the schema
it expects, which is what makes consuming unvalidated model output impossible by accident.

---

## The pipeline

Not "here is a website, summarise it". Staged, so each step operates on something
structured:

```
snapshots
  → normalise               boilerplate stripped, per-section token budget
  → extraction     [AI]     products, pricing, features       — cheap model
  → validation              Pydantic + the observed-price check
  → positioning    [AI]     audience, value props, SWOT       — strong model
  → recommendations [AI]    only when the org described itself — strong model
  → SEO signals             pure code, no AI
  → scoring                 deterministic formula
  → embeddings              chunk and store
  → comparison     [AI]     explains a matrix it did not compute
```

Two model tiers, because extraction is reading comprehension over supplied text while
synthesis is reasoning. Paying synthesis rates for extraction would roughly triple the
cost of an analysis for no gain.

---

## Provenance

Every fact carries `source: "observed"` or `"ai_inference"`, and the UI renders the two
differently — different colour, different icon, different tooltip.

* **Observed** — parsed from fetched HTML. A price, a heading, a meta description, a link.
* **AI inference** — an interpretation. A positioning statement, a strength, a
  recommendation.

The distinction is not cosmetic. It tells a user which claims they can act on directly and
which need a human to check.

---

## Never inventing a number

Two layers, because a prompt instruction alone is a request, not a guarantee.

**In the prompt.** Every prompt includes the same house rules: use only the supplied
content, never invent a number, leave a field empty rather than guessing, distinguish what
a site claims from what is true. The extraction prompt additionally receives the closed
list of prices the parser found and is told it may only use amounts from it.

**In code.** `enforce_observed_prices` runs after the model returns:

```python
amount matches an observed (amount, currency)  → kept
amount matches an observed amount, wrong currency → currency cleared, note recorded
amount matches nothing observed                → discarded, plan becomes custom pricing
```

The discarded cases produce user-visible notes ("Price for plan 'Enterprise' was not found
in the page text and was discarded") that appear on the competitor page. The user sees the
correction rather than a silently missing field.

This holds regardless of what the model does, which is the point.

---

## Prompt injection

Competitor websites are attacker-controlled input. Four layers, in
[`sanitize.py`](../apps/api/app/ai/sanitize.py):

1. **Fencing.** Content is wrapped in `<untrusted_content id="{nonce}">` with a fresh
   random nonce per request, and the system prompt states that everything inside is data
   that can never alter instructions.
2. **Neutralisation.** Zero-width and bidi characters stripped, control characters removed,
   fence lookalikes and `System:` / `Assistant:` markers defanged, pathological repetition
   collapsed.
3. **Schema validation.** Output must satisfy a Pydantic model with `extra="forbid"`. A
   successful injection still cannot produce a field the application will act on.
4. **Disclosure.** Injection-like patterns are recorded on the analysis and surfaced in the
   UI: *"This site contains text aimed at AI systems."* The user learns something true
   about their competitor.

Detection never blocks. Rejecting a page on a regex match would silently drop real data
whenever marketing copy happened to trip a pattern.

---

## Cost control

| Mechanism | Effect |
|---|---|
| Content fingerprint | Re-analysing an unchanged site reuses the previous analysis instead of paying again |
| Model tiering | Extraction on the cheap model, synthesis on the strong one |
| `analysis_depth` | `quick` / `standard` / `deep` set the page count and per-page character budget |
| Per-org quotas | Checked **before** enqueue — a queued job has already committed the spend |
| Embedding cap | At most 200 new chunks per analysis, keyed by content hash |
| Token accounting | Recorded per analysis and rolled into monthly counters |

Token spend is roughly linear in the per-page character budget, which is the dial that
actually matters.

---

## Prompt versioning

Each prompt module exports `VERSION` (`"positioning/1.0.0"`), stored on every analysis
row. Without it, a change in output quality months later is unattributable. **Bump it
whenever the wording changes.**

---

## The development provider

Selected when `AI_PROVIDER=mock` or no key is configured. It derives structured output
from crawled text using deterministic heuristics — plan names from the words preceding a
price, products from headings, weaknesses from missing meta tags.

Three things keep it honest:

* Everything it produces sets `is_mock=true`, which reaches the API and the UI as a
  persistent banner.
* It restates observations only. It never invents a price, a customer or a statistic.
* It **declines** to produce recommendations, because strategic advice is exactly the
  thing that must not come from a heuristic. The UI shows "configure an AI provider"
  instead of a panel of plausible-sounding filler.

It validates its output against the same schemas as the real provider, so a schema change
breaks it in development rather than in production.

---

## Embeddings

`VOYAGE_API_KEY` selects real semantic embeddings. Without it, a local feature-hashing
vectoriser is used: genuinely deterministic and genuinely useful for lexical matching, but
it matches shared vocabulary, not shared meaning.

Results carry `is_lexical: true` and the UI says so. The fallback exists so vector search
works out of the box rather than being an empty feature behind a missing key — but it is
never presented as more than it is.

---

## Adding a provider

1. Implement `complete_structured(spec, *, model, max_tokens) -> AIResult` in
   `app/ai/providers/`.
2. Set `name` and `is_mock`.
3. Add it to `build_provider()` and to the `ai_provider` literal in `Settings`.

Nothing else changes. The prompts, the schemas, the pipeline and the UI are all provider
agnostic.
