# 0018. Safe-text rules in the shared kernel

- Date: 2026-09-23
- Status: proposed
- Deciders: lead agent, maintainer

## Context and problem statement

Phase 3 adds many free-text fields typed by people: report descriptions, moderation
reasons, source citations, media captions. Phase 2 applied a text rule to identity names
only (NFC, strip, reject controls, surrogates and bidirectional overrides), and the Phase 2
security review asked for one shared type. Text in Urdu, Shina, Balti and Burushaski uses
Arabic or Tibetan script and needs some invisible format characters to render correctly.

Which normalisation and character rules does every free-text field follow, and where do
they live?

## Decision drivers

- One rule for every module, so a stored value is safe to log, compare, export and show.
- No "Trojan Source" reordering (CVE-2021-42574) in moderated or published text.
- Right-to-left scripts must keep the marks they need.
- Usable as a Pydantic field type under `mypy --strict`, with the lengths in OpenAPI.
- The kernel stays framework-free (standard library and Pydantic only).

## Considered options

1. Kernel `text` module with `SafeText` and a `safe_text(...)` metadata factory (chosen)
2. Keep a copy of the identity rule in each module
3. Reject every `Cf` format character as well
4. A third-party sanitising library

## Decision outcome

Chosen option: **a kernel `text` module**. Text is normalised to NFC and stripped, then
refused if it contains a `Cc` or `Cs` character (NUL included) or a bidirectional
embedding, override or isolate (U+202A-U+202E, U+2066-U+2069). LRM, RLM, ALM, ZWJ and ZWNJ
are allowed. Line breaks are refused unless a field opts in with `allow_line_breaks=True`,
which converts `CRLF` and `CR` to `LF` and accepts `LF` only.

`safe_text(max_length, min_length=1)` returns the `Annotated` metadata tuple, not a type,
because mypy accepts only aliases it can read statically:
`Description = Annotated[str, *safe_text(4000, allow_line_breaks=True)]`.

### Consequences

- Good, because every module shares one tested rule.
- Good, because Arabic-script and Tibetan-script text keeps its shaping and direction marks.
- Good, because lengths count after normalisation and show up in the OpenAPI schema.
- Bad, because other `Cf` characters (U+200B, U+FEFF) and U+2028/U+2029 still pass. They
  cannot reorder text, but they can make two strings look the same, so duplicate detection
  must not rely on how text looks.
- Bad, because identity keeps its own copy until its owner moves it to the kernel type.
  `LocalizedText` also keeps its old rule: changing it breaks an existing round-trip test,
  and it changes existing hazard and impact labels.

## Pros and cons of the options

### Kernel text module

- Good, because it has one home and uses Pydantic only.
- Bad, because the unpacking syntax (`*safe_text(...)`) is less familiar.

### Per-module copies

- Good, because modules stay independent.
- Bad, because the copies drift apart, which is the problem the security review raised.

### Reject all `Cf`

- Good, because the rule is simpler and stricter.
- Bad, because it refuses ZWJ, ZWNJ and the directional marks that Urdu and related text
  needs.

### Third-party sanitiser

- Good, because someone else maintains it.
- Bad, because it adds a kernel dependency, which the layer rules forbid, and such
  libraries target HTML, not Unicode controls.

## More information

Implementation: `src/yakhnama/shared_kernel/text.py`. The identity rules it generalises:
`src/yakhnama/modules/identity/domain/value_objects.py`.
