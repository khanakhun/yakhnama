# 0012. Catalog entries for commands, queries, DTOs and domain errors

- Date: 2026-09-23
- Status: proposed
- Deciders: lead agent, maintainer

## Context and problem statement

Every class must declare `Implements: <Pattern>` from the catalog in `AGENTS.md` §3, and the
structural test rejects names that are not listed. The catalog names the *handlers* of the
CQRS-lite flow (Command Handler, Query Service) but not the messages and results that flow
through them, nor the domain error hierarchy that §2.3 mandates. The Phase 0 standards review
found skill templates declaring `Implements: Command`, `Implements: DTO` and error classes with
no declaration at all, all of which the test would reject once copied into `src/`.

## Decision drivers

- The rule "every class declares its pattern" must be satisfiable for every class the
  architecture requires.
- Names must match the vocabulary already used in `AGENTS.md` §2.2, §2.3 and §7 (command,
  query, DTO, `YakhnamaError`).
- No new mechanism: these are Pydantic models and exceptions that already exist by design.

## Considered options

1. Add catalog rows naming the message and error types (proposed).
2. Qualify existing rows, for example `Command Handler (command model)`.
3. Exempt these classes from the declaration rule.

## Decision outcome

Proposed option: 1. Three rows are added to `AGENTS.md` §3, marked proposed until the
maintainer approves, and the structural test accepts the names:

| Concern | Pattern | Where |
|---------|---------|-------|
| Write requests | Command (imperative Pydantic model) | `application/commands.py` |
| Read requests and results | Query, DTO (Pydantic models) | `application/queries.py`, `application/dto.py` |
| Domain failures | Domain Error (exception rooted at `YakhnamaError`) | `shared_kernel/errors.py`, `domain/errors.py` |

What is needed to move to accepted: the maintainer's approval, because `AGENTS.md` §3 is
reserved to them.

### Consequences

- Good, because every architecturally required class has a name to declare, and the
  structural test stays strict.
- Good, because the names are the ones the glossary already uses.
- Bad, because the catalog grows with entries that are data shapes rather than behavioural
  patterns; the `Implements:` line on these classes documents role, not mechanism.

## Pros and cons of the options

### Option 1, add rows
Pro: honest and greppable. Con: catalog grows.

### Option 2, qualify existing rows
Pro: no new rows. Con: `Command Handler (command model)` misdescribes a request object.

### Option 3, exempt
Pro: nothing to maintain. Con: weakens the declaration rule, which §2 forbids.

## More information

Related: ADR 0011 (Settings and API Schema rows). `AGENTS.md` §2.2, §2.3.
