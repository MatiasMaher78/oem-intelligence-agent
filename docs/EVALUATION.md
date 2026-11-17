# Evaluation results and limitations

This is the main evaluation reference for the implemented OEM Intelligence Agent.
It distinguishes the closed Stage 5.5 baseline, the later local regression and
recorded real-model answers. The [README](../README.md) presents the product and
Quick Start; [architecture](ARCHITECTURE.md) explains the execution boundaries.
All fixtures, OEMs, vehicles, prices and costs are synthetic.

## Recorded outcomes and scope

| Evidence | Recorded result | Qualification |
| --- | --- | --- |
| Stage 5.5 closure: automated offline suite | **359/359 passed**, zero skips; all 11 Excel export tests executed | Closed 5.5 baseline, recorded in commit `267a8af6904560c0d4bb509712612235b1054b31`. |
| Stage 5.5 closure: scripted scenarios | **5/5 passed** | Authored deterministic ground truth and simulated provider protocol; not real-model accuracy. |
| Stage 5.5 closure: negative controls | **12/12 passed** | Forbidden/malformed calls, missing evidence, pagination, budgets and simulated provider failures. |
| Later local regression after inventory-report presentation changes | **367/367 passed**, zero skips; **5/5** offline scenarios and **12/12** controls | Windows/Python 3.13.4 verification after adding eight report/regression tests. Recorded during local pre-commit verification; no new CI execution is claimed. |
| Real E1, Groq Free, `qwen/qwen3.8-27b`, skill **1.0.1** | **Approved: 10/10 expected structured fields**, complete final answer | One synthetic question; does not validate all scenarios or the later skill. |
| Real E2, same model, skill **1.0.2** | **Partial / not approved** | Additional exploration and preventive quota block; no final answer. |
| Real E5, same model, skill **1.0.2** | **Partial / not approved** | Preventive block before tool results reached the model; no final answer. |

The local regression extends the suite; it does not change the historical 359-test
closure. It includes 11 Excel export tests and the separate core-without-Excel
dependency test. The earlier recorded Ubuntu/Python 3.11
[CI run](https://github.com/MatiasMaher78/oem-intelligence-agent/actions/runs/42390123816)
belongs to the baseline, not a new 367-test hosted run. The
[CI configuration](../.github/workflows/ci.yml) installs the Excel extra and uses
test discovery plus the offline evaluator.

E2/E5 are closed without further inference or optimization. This document grants
no authorization to repeat them or contact a provider. Earlier partial attempts
remain in the [detailed 5.5 record](STAGE_5_5.md); they are not additional successful
cases. Earlier [5.3 business smoke questions](STAGE_5_3_GROQ.md) are a separate
functional check, not E1/E2/E5 passes in this independent evaluation.

## Independent dataset and deterministic ground truth

[stage5_eval_data.py](../oem_agent/stage5_eval_data.py) defines five independent
one-piece fixtures, IDs 1001-1005, without extending the 27-piece inventory. It
uses the existing SQL, matching, statistics and dossier functions. OEMs remain
literal strings, including leading zeros and separators.

| Scenario | OEM | Authored evaluation expectation |
| --- | --- | --- |
| E1: possible price anomaly | `0000421001` | Ten comparable ads, 55 units, observed EUR 10-100 and filtered EUR 20-90. The question's hypothetical EUR 190 price exceeds the DEMO band; this is not a stored inventory price or recommendation. |
| E2: independent matching axes | `EV5A0102B` | Four audited offers: exact/incompatible, exact/unknown compatibility, unknown OEM/confirmed compatibility and different OEM/confirmed compatibility. No usable comparable sample; cataloguing remains unassessed. |
| E3: competitive within a DEMO band | `EV5-AB/03.01` | Ten comparable ads, 20 units and filtered EUR 60-130. The question's hypothetical EUR 65 lies within that band; no pricing action follows. |
| E4: successful absence | `9990000004` | Both sources succeed with no offers. Known zero ads/units differs from absent prices; no catalog-error diagnosis. |
| E5: marketplace failure | `EV5-X/05` | Source A fails; source B retains five known ads/15 units. Coverage stays partial; filtering and DEMO cost are withheld. |

`GROUND_TRUTH` contains authored literals, not model-generated answers. Tests
cross-check them against the deterministic pipeline. Dataset version is
`oem-eval-5.5-v1`; its recorded SHA-256 is
`9b81e56398055d431c9cd3a1c67e9951bb779ed1c56e1b185923a9bae84bc548`.
The historical 5.1 E3 proposal had nine offers; the implemented ten-offer scenario
is the ground truth above. That earlier plan is preserved as history.

## What the checks establish

The [evaluator](../oem_agent/stage5_eval.py), version **5.5.1**, checks exact
structured facts/types, required accepted reads, references, abstentions,
coverage, errors/limits and trace/configuration integrity. Matching evidence may
be an exhaustive verified aggregate or a complete granular audit; an unfinished
page sequence cannot be treated as complete. The rubric does not require a fixed
tool sequence and does not route the LLM's business questions in Python.

Offline transcripts deliberately echo authored facts. They exercise the real
protocol normalization, loop, readers and dispatcher, including negative controls;
they do not measure model reasoning, language quality or general business accuracy.
The twelve controls cover forbidden shell/approval, malformed arguments, unknown
inventory, repeated reads, reduced budgets, unfinished pages, missing review and
simulated 429/auth/context/time failures.

Execution completion, source coverage and answer correctness are distinct:

- `complete` describes the finalized bounded execution, not semantic correctness.
- E5's known offers survive the source error. A useful explanation can acknowledge
  partial evidence, but neither that explanation nor resolvable references makes
  source coverage complete. The recorded real E5 run produced no final explanation.
- `references_verified` establishes source identity and pointer resolution, not
  correctness of every claim or its interpretation. `answer_verified` remains
  false and free prose requires human review.

The current skill is **1.0.2**; E1 used **1.0.1**. Contract **5.1** and configuration
format **5.4.0** version different components. A small, selected, mixed-skill
sample supports no statistical reliability percentage or general accuracy claim.
No cost saving or productivity improvement was measured.

## Real-run metrics are historical observations

The latest recorded real executions used Groq Free and native model-selected
calls. Latencies exclude initial operator preflight.

| Scenario | Inference POSTs | Read calls | Reported input + output tokens | Agent latency |
| --- | --- | --- | --- | --- |
| E1 | 3 | 2 | 4815 + 251 = **5066** | 3.483 s |
| E2 | 2 | 3 | 3817 + 129 = **3946** | 2.451 s |
| E5 | 1 | 3 | 1229 + 117 = **1346** | 1.646 s |

Reported usage is separate from preventive prompt/output/safety reservations.
Byte estimates are not real tokens. E2/E5 preventive blocks do not prove a provider
429, exhausted daily quota or incorrect final-model facts; they prevented a final
answer and therefore prevented approval. Unknown remaining balances are not
unlimited. The historical Free caps were 30 RPM / 1000 RPD / 8000 TPM / 200000 TPD;
any future separately authorized run requires fresh operator/account checks.
There is no automatic retry, paid-provider fallback or billing change.

## Reproduce offline without credentials

Prepare the isolated environment and existing `.[excel]` extra using the
[Quick Start](../README.md#quick-start). From the repository root, use that
virtual environment's interpreter.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m oem_agent.stage5_eval offline --out runs/eval-docs-01
```

Linux:

```bash
./.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
./.venv/bin/python -m oem_agent.stage5_eval offline --out runs/eval-docs-01
```

No API key, `.env`, local model or paid service is needed. Linux needs its
distribution's Python venv support. Package installation may use the Python
package index; the tests/evaluator use local synthetic evidence and simulated
provider frames. Without the Excel extra, 11 export tests are skipped, so that
is not the full no-skip verification.

Choose a new `--out` directory on repeat runs. The evaluator exclusively creates
dataset identity, evidence/dossiers, scripted agent traces, scores, controls and
summary. Inputs reproduce the same dataset/ground-truth identity and check results;
timestamps, timings and complete trace/report hashes can vary.

## Evidence availability and limitations

The [detailed Stage 5.5 record](STAGE_5_5.md) preserves reviewed results, reservation
examples, matching-summary measurements and dated verification history. Generated
SQLite files, XLSX, review revisions and raw real traces stay excluded from Git.
Public tables record the reviewed historical outcomes; regenerating offline
artifacts does not replay or independently reproduce hosted model answers.

Fixtures do not establish real-market coverage or real vehicle fitment. DEMO
percentiles/costs have no measured commercial or statistical validity. Tools stay
read-only; dossier decisions use a separate local operator CLI with self-declared
identity and no ERP/authentication integration. No evaluation, approval state or
model answer triggers an automatic commercial operation.
