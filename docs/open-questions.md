# Open questions

This log holds domain and product decisions that are not yet settled, so that nobody has
to invent a fact to keep working (`AGENTS.md` §5: "Never invent domain facts"). When a
change depends on something unsettled, add an entry here instead of guessing, and reference
it from the code or documentation that depends on it.

Each entry has:

- **Question** — the decision that needs to be made.
- **Why it matters** — what depends on it, or breaks without it.
- **Proposed default** — what we build against until the maintainer decides, if a default
  is safe to assume; marked non-blocking unless stated otherwise.
- **Blocking** — whether work is stopped until this is answered (`yes`) or can proceed on
  the proposed default (`no`).
- **Status** — `open`, `answered` (with the answer and date), or `superseded`.

---

## Q1 — Administrative boundary data source

**Question:** Which administrative boundary dataset do we use for Place records (country →
province/region → district → tehsil → union council → village)?

**Why it matters:** Every `Place` in the system links to a boundary and hierarchy; changing
source later means re-linking every place.

**Proposed default:** OCHA COD-AB (Common Operational Datasets, administrative boundaries)
for Pakistan, via HDX (Humanitarian Data Exchange).

**Blocking:** no

**Status:** open

---

## Q2 — Final hazard taxonomy labels and local-language names

**Question:** What are the final hazard type labels (IRDR-aligned) and their names in
Urdu, Shina, Burushaski, Balti, Wakhi and Khowar?

**Why it matters:** `Hazard type` codes are retired, never reused or deleted once
published, so the taxonomy needs to be right before it ships broadly.

**Proposed default:** None; hazard types are added one at a time via the `add-hazard-type`
skill as they are confirmed, English label only until local names are supplied.

**Blocking:** no

**Status:** open

---

## Q3 — Public coordinate rounding precision

**Question:** How many decimal places of latitude/longitude are shown on public endpoints
and exports, to protect reporter locations?

**Why it matters:** Too much precision can reveal a reporter's home or exact vantage point;
too little makes the dataset less useful for hazard mapping.

**Proposed default:** 2 decimal places (~1.1 km at the equator). Configurable via
`Settings.public_coordinate_decimals`.

**Blocking:** no

**Status:** open

---

## Q4 — Data licence

**Question:** What licence covers the published dataset (as opposed to the code)?

**Why it matters:** Determines who can reuse the data and how they must attribute it;
affects the licence sidecar shipped with every export.

**Proposed default:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), over
alternatives such as ODbL.

**Blocking:** no

**Status:** <!-- maintainer decision pending --> open, pending final maintainer confirmation

---

## Q5 — Production identity provider and hosting target

**Question:** Does production run on Keycloak (as used locally via `poe up`) or Zitadel,
and where is it hosted?

**Why it matters:** `platform/auth` is built against OIDC/JWKS either way, but operational
details (token lifetime defaults, admin bootstrap, hosting cost) differ.

**Proposed default:** Keycloak, matching local development; hosting target undecided.

**Blocking:** no

**Status:** open

---

## Q6 — Code licence final confirmation

**Question:** Is Apache-2.0 the final code licence?

**Why it matters:** `LICENSE` and `pyproject.toml` already declare Apache-2.0; this entry
tracks that it is a recommendation pending explicit maintainer sign-off, not yet a closed
decision.

**Proposed default:** Apache-2.0, recommended over MIT for its explicit patent grant and
NOTICE-file handling, both of which suit an infrastructure project that expects
institutional contributors.

**Blocking:** no

**Status:** <!-- maintainer decision pending --> open, pending final maintainer confirmation

---

## Q7 — Authoritative source for the glacier and glacial-lake counts

**Question:** What is the authoritative, citable source (for example the ICIMOD glacier
inventory or a GLIMS extract, with year) for the "more than 13,000 glaciers and thousands
of glacial lakes" figures used in the project's mission statement?

**Why it matters:** These figures currently come from the maintainer's project brief, not a
cited inventory; an uncited count in the public README affects the credibility of the
project's public record.

**Proposed default:** Cite the ICIMOD glacier inventory once the specific edition and year
are confirmed.

**Blocking:** no

**Status:** open

---

## Q8 — Licensing of third-party data mixed into exports

**Question:** How is licensing handled for exports that combine Yakhnama's own CC BY 4.0
data with ingested third-party data under different terms (for example OpenStreetMap data
under ODbL, or boundary datasets with their own terms)?

**Why it matters:** Yakhnama cannot relicense third-party content under CC BY 4.0; without a
rule, some export combinations may be legally impossible to publish under a single stated
licence. This blocks ADR 0010 moving from `proposed` to `accepted` (see ADR 0010,
Consequences).

**Proposed default:** Keep the per-source licence attached to each record or layer, and list
every distinct licence present in the export's metadata sidecar, rather than collapsing them
into one licence statement.

**Blocking:** yes, for accepting ADR 0010

**Status:** open

---

## Q9 — Contributor sign-off and `NOTICE` copyright line

**Question:** Do contributions require a Developer Certificate of Origin (DCO) sign-off or a
contributor licence agreement, and what copyright-holder line goes in the `NOTICE` file?

**Why it matters:** ADR 0010 lists this as one of the items the maintainer must decide before
the licensing ADR can be accepted.

**Proposed default:** DCO sign-off (`Signed-off-by:` trailer) on every commit, and a `NOTICE`
copyright line reading "Yakhnama contributors".

**Blocking:** yes, for accepting ADR 0010

**Status:** open

---

## Q10 — Anonymous or assisted citizen reporting under OIDC-only authentication

**Question:** Given ADR 0005 (OIDC-only authentication), how does an anonymous member of the
public, or someone assisted by another person, submit a report without an account?

**Why it matters:** OIDC-only authentication (ADR 0005) implies every actor has an identity
provider account; community reporting from people without one, or who need assistance
submitting, is a use case the current auth model does not yet cover.

**Proposed default:** Require an account to submit a report for now; revisit anonymous or
assisted submission when the reports module is built.

**Blocking:** no

**Status:** open

---

## Q11 — Pattern-catalog additions proposed in ADR 0011 (Settings, API Schema)

**Question:** Does the maintainer approve adding the Settings and API Schema entries to the
pattern catalog, as proposed in ADR 0011?

**Why it matters:** `AGENTS.md` §3 (the pattern catalog) is protected and changes only with
maintainer approval; ADR 0011 proposes additions to it.

**Proposed default:** None; the additions stay proposed until the maintainer approves the
catalog change.

**Blocking:** no

**Status:** open
