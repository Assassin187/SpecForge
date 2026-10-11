# RQ2: independent generic specifications versus native protocol specifications

This suite implements the approved MQTT study entirely inside `expriments/RQ2`.
The SpecForge core, native prompts/schemas, cases and evaluator are unchanged.
It creates fresh specifications: it never reads a historical P bundle to form G.

| Arm | Generation |
|---|---|
| D | Original task, requirements and MQTT standard → common Coder |
| G | Original inputs → independent native Facts → independent SYSSPEC design/contracts → fresh semantic review → publication → common Coder |
| P | Original inputs → native Facts/Design/Specs/review/publication → common Coder |

`generic_planner.py` subclasses the native Pipeline. Facts delegates to the
original method, prompt, schema and gate. Its G-only jobs use the native Agent
loop and ToolRuntime with explicit local prompts/checks. `NativePlanning`
records native job start/end/wall time and applies the frozen Design output
allowance. P retains its native prompts, schemas, tools, gates and Agent loop.
`spec_adapter.py` checks and publishes G's engineering contracts and describes
each arm's delivery metadata. It copies published bytes without converting
semantics, inventing protocol fields, importing another arm's design or using
the SpecFS code generator. G is an independently generated **SYSSPEC baseline**,
not a reproduction of an undocumented SpecFS raw-input planner.

G controls its own layout, ABI, functions, algorithms and protocol reasoning.
Its project/file/function `.spec` documents use `[PROMPT]`, `[RELY]`,
`[GUARANTEE]`, `[SPECIFICATION]`. Precise bytes, protocol states, error priority,
examples, test expectations, calling conventions and ownership are unrestricted.
`plan.json` is a navigation index; it is not P's protocol schema. Engineering
checks require valid referenced artifacts, requirement coverage, exact planned
signatures and compilable public headers. Semantic review uses a fresh session
and per-requirement evidence, after removing author review claims/checkpoints.
Passing a specification check or review does not establish behavioral correctness.

P uses its existing protocol structure, wire mapping, call contracts, planning
vectors, traceability, ABI checks and release mechanism. G/P each generate their
own Facts, designs, specs and review; no model-generated results are shared.
Both can repair their own specs once on an evidenced development gap using
approved own Facts. Each revision gets a new review and publication. No Facts
restart or extra independent design stage is granted during repair.

All jobs use the native `deepseek-flash` profile. For new runs, only Design
has a single-response output limit of 131,072 tokens (twice the native 65,536);
Facts, Specs, review, code and repair keep 65,536. The RQ2 adapter applies the
same Design allowance to G/P using a local model copy, without changing native
configuration or another job. The override is frozen as `stage_models` and
recorded in requests and summaries. Thinking, transport retry and checkpoint
behavior are unchanged. G/P upstream response limits are
Facts 40, Design 100, Specs 180, independent review 60. Each arm has initial code
180 and at most three implementation repair jobs of 40. G/P may spend another
180 + 60 on one specification repair/review. Checkpoint responses count.
Adjusting code after a specification revision consumes a counted repair job.
There is no automatic replacement of failed/interrupted attempts or extra trial.
Equal resource opportunities do not imply equal actual tokens; costs are measured.
D receives the complete unchanged raw task, including planning requirements,
and can plan internally. Its total upstream opportunity differs; P−G is primary.

The common Coder can compile, self-test and edit from its first response. All
arms use `coder.md`, the neutral delivery schema and the same native development
verifier (normal/self-test, ASan+UBSan/self-test, final normal rebuild).
G/P Coder mounts only its published specs/ABI, own project and development
reports. D mounts raw inputs and deterministic extracted standard instead.
No generated Facts, other-arm outputs, history or independent evaluator are
mounted for G/P coding. Native tool resolution and bubblewrap isolate both
file tools and shell commands; accepted peers in a test must run in one command
because each command has a fresh network namespace.

The initial snapshot is saved **after the initial code job** and before extra
code/spec repairs. It includes that job's development feedback and edits, and
can be incomplete. Acceptance starts only after all attempts stop. Both initial
and final snapshots are evaluated on copies with all 16 frozen MQTT scenarios,
in normal and sanitizer modes. Upstream/development failure does not remove an
attempt: any retained project with a Makefile is evaluated; absent projects and
missing/duplicate/skipped/environment-blocked scenarios fail with explicit
reasons. Independent results cannot trigger generation or repair.

## Commands

Run from `/home/ljf/SpecForge` with its pinned dependencies and native model key
environment. Preparation, help, tests and summaries do not create model clients.
`prepare` checks required tools and isolation, freezes inputs/runtime/evaluator,
records fingerprints and a seed-shuffled **serial** order. The seed controls
execution order, not model sampling. The first study permits one attempt per
selected condition and MQTT only. Historical schema-1 runs cannot resume here.

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s expriments/RQ2/tests -v

python3 -B expriments/RQ2/run.py prepare \
  --protocol mqtt --model deepseek-flash --conditions D G P --repetitions 1 \
  --out expriments/RQ2/runs/mqtt_e2e_001
python3 -B expriments/RQ2/run.py generate --run expriments/RQ2/runs/mqtt_e2e_001
python3 -B expriments/RQ2/run.py evaluate --run expriments/RQ2/runs/mqtt_e2e_001
python3 -B expriments/RQ2/run.py summarize --run expriments/RQ2/runs/mqtt_e2e_001
```

Changes to the frozen suite/core/control inputs invalidate further commands.
An interrupted generation records the spent attempt, retains logs and continues
only pending arms on the next invocation. It does not restart that arm's budget.
Evaluation is terminal for generation. An interrupted evaluator is preserved
as an incomplete evaluation, rather than silently rerun.

`summary/results.json`, `trials.csv`, `stages.csv`, `scenarios.csv` and `README.md`
include real upstream/downstream/review/repair usage, phase wall times, stopping
reasons, initial/final acceptance, edits, feedback and artifact links. API failure
with unknown usage remains unknown; recorded usage is a subtotal. Truncated
responses count, and SDK retries are not individually observable. Reasoning
tokens are already included in output tokens. Edits are operations, not defects;
spec-gap references are reported evidence, not demonstrated causal attribution.

This one-attempt-per-arm study provides MQTT case evidence. Scenarios, two build
modes and two snapshots are not independent samples; this suite does not claim
significance, cross-protocol advantage or stochastic stability. Independent
architectures mean P−G tests specification formation/handoff as a mechanism,
not a file-format-only transformation. A P win is not an implementation criterion.

## Archive and isolation baseline

`archives/legacy_20261009T152647Z/` holds the unchanged old tree in `legacy/`,
verified `legacy.tar.gz` and external source-run snapshots, the original paths,
file hashes/modes/symlinks, Git head/status/diff, protected-system hashes and a
baseline tar. All 21,280 old files (626,902,178 bytes), including failed records
and duplicate paper runs, were preserved. External source runs remain in place.
Old JSON absolute paths and bytes were not rewritten. `VERIFIED.json` records
the archive checks. Archive/run artifacts are ignored by Git, not deleted.
Baseline checks compare the current core/prompts/schemas/tests/cases/assets and
evaluator with the preimplementation bytes, including existing user changes.
