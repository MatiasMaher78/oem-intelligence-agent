# Repository agent instructions

## Mission
Teach AI Agent / LLM / harness engineering on synthetic automotive OEM inventory evidence.

## Mandatory process
1. Read relevant code and tests first. Explain a short plan before touching files.
2. Verify the actual file paths and git diff, not assumptions. Never print secrets.
3. Make changes that are isolated, additive, reversible, and tested. Stop on surprises.
4. Run `python -m unittest discover -s tests -v` before proposing a commit.
5. Stage explicitly selected paths; never use `git add .`.

## Policy gates
- Never copy confidential employer material, real DBs, code, prices, OEM lists or scraping credentials here.
- All data remains synthetic. No real network scraping, unauthorized endpoints, or write access to third-party sites.
- Business math and exact OEM matching must be deterministic and test-covered.
- Query inventory read-only. No autonomous DB mutations or pricing decisions.
- LLM explanations are **advisory**; do not let model-generated numbers overwrite audited evidence.
- Do not expand tool permissions, enable scheduled paid calls, or activate external services without explicit approval.

## Definition of done
- Feature is demonstrated with a fresh synthetic DB.
- Offline tests pass and outputs contain source evidence and an execution trace.
- Documentation and public-safe status are updated.


## Instruction ownership
- This file governs repository work and is not sent to the runtime model.
- The current Stage 5 agent loads only `oem_agent/skills/oem-evidence-review/SKILL.md`
  as its instructions. Update that versioned source instead of copying a prompt.
- Python contracts/dispatcher define permissions and bounds; a skill grants none.
- Earlier delivery/decision documents describe their historical increment. The
  original two-tool SDK demo is isolated from the current five-tool agent.
- Current context, provenance and source-reference semantics: `docs/STAGE_5_4.md`.
