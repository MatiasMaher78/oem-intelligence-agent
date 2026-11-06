# Stage 5.4: one instruction source and auditable context

Stage 5.3 is accepted in local commit `20fbc6b701bf798f0b7e53470e3289c363eb0778`.
Its 19 selected source/document/test files exclude credentials, generated data
and reports. The 268-test baseline passed before committing. Stage 5.4 is separate
and remains uncommitted pending review. No real provider call is part of 5.4.

## Instruction ownership and scope

- AGENTS.md: developer/repository process. Never injected into LLM context.
- [OEM evidence skill](../oem_agent/skills/oem-evidence-review/SKILL.md): the single
  current runtime instruction source, name oem-evidence-review, version 1.0.0.
- stage5_contracts/stage5_dispatcher: enforced schemas, permissions and budgets.
  The skill has no ability to add tools, writes, providers or authorization.
- Delivery/architecture decision/5.3 documents: scoped implementation history,
  not extra model instructions. The original deterministic and optional two-tool
  SDK demo remain isolated and unchanged.

The two previous skill files were removed after caller inspection: they were not
loaded by the current agent and contained assumptions from the original demo
(90-day/unverified selection, market null/zero rules). They are not applied to the
current 60-90-day evidence pipeline. Business rules themselves are unchanged;
the new skill queries existing evidence without re-declaring selection math.

## Compact, reproducible context

stage5_context reads only the packaged SKILL.md, bounded to 4 KiB and a body of
at most 1600 UTF-8 bytes, checks name/version and excludes frontmatter. At process
startup the skill is loaded once; each session records the identity of that exact
body. Restart the process after updating a skill. Missing/malformed/oversized
resources fail closed. Runtime loading uses standard-library importlib.resources;
no YAML dependency or dynamically included instructions are added. The portable
skill also passes the available skill-creator validator.

The current prompt body is 778 bytes versus 792 in accepted 5.3. Only that body,
the question, native schemas and existing compact result projections go to the
selected model. No AGENTS, historical documents, manifest dumps, source appendices
or configuration trace is added to the model's conversation. R source labels are
attached after generation. No history pruning or new summarizer is introduced.
All model decisions remain native tool calls, not Python question routing.

pyproject package-data includes the sole skill under the Python package. The local
ZIP-resource check passed using the configured package-data and archive importer,
without network, source-tree imports, installs or environment changes. A full wheel
was not built: setuptools/wheel are not installed here, and no dependency was added.

## Configuration provenance

Each stage5-04 report includes configuration and configuration_sha256:

- configuration.version = 5.4.0;
- skill name/version/file SHA-256 and SHA-256 of the actual system prompt;
- contract version and fingerprint of the complete five schema definitions;
- explicitly selected provider/model, execution and dispatcher limits;
- Groq local batch ceilings and operator-confirmed Free limit caps when available.

The fingerprint is stable for the same effective configuration, and changes when
instructions, model, schemas or limits change. Dynamic timestamps, remaining
balances and usage stay in existing execution traces instead of configuration
identity. No environment serialization, key/header dump, raw HTTP body, thinking
content or private reasoning hash is introduced. Configuration identity remains
available for failed runs. Fingerprints are integrity aids, not signatures or
independent proof of provider billing status.

Existing traces retain every tool attempt/result/reference, real versus estimated
token metrics, time and sanitized errors. This increment does not alter the
provider transports, quotas, five contracts, dispatcher or deterministic pipeline.

## Verifiable answer references

After existing completion/coverage checks, the runtime gathers sources only from
actual accepted reads with data. It resolves every hash-bound JSON Pointer against
the pinned evidence/dossier again, deduplicates identical references and assigns
stable first-use labels R1, R2, etc. The report contains:

```json
{
  "answer_references": [
    {"id": "R1", "reference": {"document": "evidence", "sha256": "SOURCE_SHA256", "pointer": "/results/1"}}
  ],
  "references_verified": true,
  "answer_verified": false
}
```

The answer receives a compact local footer with those labels; the structured
reference is directly compatible with snapshot.resolve_reference. Full per-tool
references and hash-linked traces are retained. In the two-page costs case, all
27 evidence pointers and one deduplicated dossier root survive (28 references).
No synthetic report sources or unrequested reads are invented to fill citations.

Missing or invalid references withhold an answer and prevent complete=true.
An incomplete page sequence remains partial even when its existing references
resolve. Missing review stays unavailable. references_verified means source
identity/resolution only; semantic prose/numerical accuracy is still separate,
advisory_only=true and answer_verified=false. No commercial human approval is
recorded by an evaluation or reference check.

## Validation and limits

Tests use simulated native protocol responses and existing fresh synthetic
fixtures, never an API key or running model. Checks cover the sole compact skill,
malformed resources, configuration stability/changes and safe fields, matching
actual sent prompt/schema identities, exact/resolvable sources, 27-case reference
coverage, missing/foreign references, partial pages, failures, forbidden tools,
private-field omission and existing exclusive/fingerprinted export.

Detailed counts, timings, preservation and security checks are recorded in the
new ignored directory `runs/stage5-4-consolidation-20261105-38c0fa48/`.
The original cases, stock selection, SQL, statistics, DEMO costs and human
expedients are unchanged. Earlier real Groq evidence remains valid for accepted
5.3; it is not a real evaluation of the changed 5.4 prompt. New real inference
requires explicit user authorization. No commit/publication of 5.4 is performed.

## Final offline results (2026-11-05)

- `python -m unittest discover -s tests -v`: 283 passed in 22.969 seconds,
  including the 268 unchanged earlier tests and 15 context/provenance regressions.
  ResourceWarning was configured as an error; no open-resource warning occurred.
- Skill-creator validation and standard-library ZIP resource loading passed.
  Prompt: 778 UTF-8 bytes; previous 5.3 prompt: 792 bytes. These are sizes, not tokens.
- Fresh disposable databases reproduced six original cases/seven reads and all
  27 Stage 2 selections. All Stage 3 result records equal the prior deterministic
  evidence: one sufficient DEMO sample, two insufficient, 24 without comparables.
  DEMO review remains 26 blocked, ID 10 eligible, all 27 pending human review.
- Simulated two-page native costs calls (20/7 pieces) produced a complete report
  with 28 resolvable references, configuration identity and retained traces. Its
  protocol timings/tokens are fake test metrics, not real-provider measurements.
- Hash checks preserved 124 previously recorded generated artifacts and 17 files
  from the latest accepted real third-question run, including the pinned inputs.
- Secret-pattern checks covered 245 source/text-report files with no findings;
  actual .env was never read, printed or hashed. .env/variants remain ignored and
  untracked, .env.example has no key value, generated data/reports remain excluded.
  Pattern scanning cannot prove absence of every arbitrary secret.
- HEAD remains the selective 5.3 commit, index empty, no remotes or publication.
  Stage 5.4 has 14 pending paths (including two retired skills); it is uncommitted.

Review evidence: stage54-tests.txt, stage54-test-summary.json,
package-resource-check.json, deterministic-regression.json,
stage54-simulated-agent-report.json, preservation-security-review.json and
review.md in the ignored verification directory cited above.
