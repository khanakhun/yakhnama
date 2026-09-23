# 0010. Licensing of code and data

- Date: 2026-09-23
- Status: proposed
- Deciders: maintainer (final confirmation pending)

## Context and problem statement

Yakhnama publishes two different things: source code for the backend, and an open dataset
of hazard events, impacts, places and sources. Each needs a licence that lets humanitarian
agencies, government bodies, universities and research institutes adopt, contribute to and
redistribute it with as little legal friction as possible. The maintainer has offered MIT
or Apache-2.0 for the code and wants maximum adoption by humanitarian and research
organisations.

Which licence applies to the code, which to the data, and how is the data licence carried
with every export?

## Decision drivers

- Maximum adoption by humanitarian and research organisations, including those whose legal
  teams review every dependency.
- Institutional contributors: clear patent terms and attribution notices.
- Reuse of the dataset alongside other humanitarian and scientific datasets.
- Provenance: every exported file must say under what terms it may be used and how to cite
  it.
- Simplicity for a small team: widely known, standard licences only.

## Considered options

Code:

1. Apache-2.0 (proposed)
2. MIT
3. AGPL-3.0

Data:

1. CC BY 4.0 (proposed)
2. ODbL 1.0

## Decision outcome

Proposed option: **Apache-2.0 for code and CC BY 4.0 for data**, with the licence written
into every export's metadata sidecar.

- **Code, Apache-2.0.** It is as permissive as MIT for users, and adds an explicit patent
  grant with patent-retaliation termination, a `NOTICE` mechanism for attribution, and a
  clause that grants no trademark rights to the project name. These points matter to
  institutional contributors and are why Apache-2.0 is common for infrastructure software.
  AGPL-3.0 is not proposed, because the maintainer offered MIT or Apache-2.0 and its network
  copyleft would deter some of the organisations we want to adopt the platform.
- **Data, CC BY 4.0.** Attribution-only terms are simple for humanitarian reuse. ODbL's
  share-alike obligation on derived databases complicates combining Yakhnama data with
  other datasets under different terms.
- **Exports.** Each export carries its licence identifier, licence URL, required attribution
  text and citation in its metadata sidecar. The sidecar format is defined in the phase that
  builds the exchange module.

**What is needed to move this ADR to `accepted`:**

1. Written confirmation from the maintainer of Apache-2.0 for code.
2. Written confirmation from the maintainer of CC BY 4.0 for data.
3. A maintainer-decided rule for third-party data whose licence differs from CC BY 4.0 (see
   Consequences), recorded in this ADR or a follow-up ADR.
4. The maintainer's decision on the `NOTICE` file contents (copyright holder line) and on
   whether contributions need a Developer Certificate of Origin sign-off or a contributor
   licence agreement.

Once those are recorded, the status line changes to `accepted` and the index in
[README.md](README.md) is updated. The repository already contains an Apache-2.0 `LICENSE`
and `license = "Apache-2.0"` in `pyproject.toml`; if the maintainer chooses otherwise, both
must change (the `LICENSE` file is protected and changes only with maintainer approval).

### Consequences

- Good, because Apache-2.0 is accepted by most institutional legal reviews and its patent
  grant protects both contributors and users.
- Good, because CC BY 4.0 lets anyone reuse, combine and redistribute the dataset with
  attribution, which suits humanitarian response and academic work.
- Good, because a licence in every sidecar means files carry their terms even after they are
  copied far from the platform.
- Bad, because a permissive code licence allows anyone, including commercial operators, to
  run a modified closed version without sharing improvements back.
- Bad, because CC BY 4.0 does not require derived datasets to stay open, so improvements to
  the data made by others may not flow back.
- Bad, because Yakhnama will ingest third-party data that keeps its own licence: for example
  OpenStreetMap data is under ODbL, and boundary and humanitarian datasets have their own
  terms. We cannot relicense such content under CC BY 4.0, so exports that include it must
  carry per-source licence information, and some combinations may be impossible to publish
  under a single licence. This is why item 3 above blocks acceptance.
- Bad, because Apache-2.0 is incompatible with GPL-2.0-only code, so such dependencies cannot
  be used.
- Bad, because until this ADR is accepted, the code and data terms are formally undecided,
  and external contributors may hesitate.

## Pros and cons of the options

### Apache-2.0 (code)

- Good, because of its explicit patent grant, `NOTICE` mechanism and trademark clause.
- Bad, because it is longer than MIT and requires keeping `NOTICE` and change notices.

### MIT (code)

- Good, because it is the shortest, best-known permissive licence.
- Bad, because it has no explicit patent grant, which some institutional contributors'
  legal teams require.

### AGPL-3.0 (code)

- Good, because modified versions offered over a network must share their source.
- Bad, because many organisations avoid AGPL dependencies, which conflicts with the goal of
  maximum adoption, and it is outside what the maintainer offered.

### CC BY 4.0 (data)

- Good, because it is attribution-only, internationally recognised and widely used for
  open research and humanitarian data.
- Bad, because it has no share-alike requirement.

### ODbL 1.0 (data)

- Good, because share-alike keeps derived databases open, and it is designed specifically
  for databases.
- Bad, because share-alike complicates mixing with datasets under other licences, which is
  common in humanitarian analysis.

## More information

- Apache License 2.0, apache.org/licenses/LICENSE-2.0.
- Creative Commons Attribution 4.0 International, creativecommons.org/licenses/by/4.0.
- Open Data Commons Open Database License 1.0, opendatacommons.org/licenses/odbl.
- Open questions on licensing are tracked in `docs/open-questions.md`.
