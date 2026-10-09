# MQTT/CoAP RQ2: intermediate Spec utility

This suite prepares and runs 15 independent generations: three repetitions of
Direct, Generic, Full, Full−Vectors and Full−Contracts. Its default uses MQTT and the
frozen publication at runs/paper/round_03/mqtt_01/specs/r002. That publication
already incorporates historical coding feedback; this experiment never revises
the supplied specifications.

`prepare --protocol coap` uses the approved third-round CoAP publication at
`runs/paper/round_03/coap_01/specs/r001`. It passed specification review without
coding-feedback revision. Its historical formation cost includes Facts, Design,
Specs and initial review; the subsequent implementation is excluded. MQTT and
CoAP have different frozen specification histories and acceptance scenarios;
comparisons are made within each protocol.

Materialized copies of the selected SpecForge publication and its corresponding
specfs-format Generic specifications are stored together under `specs/`.
See [the snapshot inventory](/home/ljf/SpecForge/expriments/RQ2/specs/README.md)
for their locations, conversion rules and recorded hashes.

No formal experiment is started by installation, help, tests, preparation or
reporting. Only the generate command invokes the model and incurs new model
usage. Preparation checks the publication, dependencies, source provenance,
isolated execution and all input hashes.

## Run

Use the Python environment containing the repository's fixed dependencies,
openai 1.97.0 and jsonschema 4.24.0. GCC, Make and bubblewrap are required.
MQTT also requires pdftotext, mosquitto_pub and mosquitto_sub; CoAP requires
coap-client-notls for its independent interoperability scenario.

    cd /home/ljf/SpecForge
    python3 -B expriments/RQ2/run.py --help
    python3 -B expriments/RQ2/run.py prepare --out expriments/RQ2/runs/mqtt_001

Review experiment.json, control/direct_task.diff, the transformation records
and the frozen views before generation. Supply the model credential through
DS_API in the environment; do not put it in a command argument or file.

    python3 -B expriments/RQ2/run.py generate --run expriments/RQ2/runs/mqtt_001
    python3 -B expriments/RQ2/run.py evaluate --run expriments/RQ2/runs/mqtt_001
    python3 -B expriments/RQ2/run.py summarize --run expriments/RQ2/runs/mqtt_001

Generation is serial, in three precomputed randomized blocks with scheduling
seed 20261008. Each trial starts in a new project and conversation. Every
condition uses deepseek-flash, high reasoning, thinking enabled, nonstreaming,
65,536 maximum output tokens, 180 initial responses and at most three
40-response implementation repairs. The seed controls order, not model
sampling. The shared native executor retains its existing transport retry and
context reset behavior.

For a pilot with one generation per condition, prepare with `--repetitions 1`.
Generate with `--workers 5` to run the five conditions concurrently, then use
the same evaluate and summarize commands. The default remains three repetitions
and serial generation. Each parallel worker has its own project, model dialogue
and mutable trial state; synchronized progress snapshots preserve all attempts.
Parallel execution is recorded because timing includes shared API/host contention.

To run only Direct/Generic/Full once, using three parallel workers:

    python3 -B expriments/RQ2/run.py prepare --out expriments/RQ2/runs/mqtt_dgf_001 --repetitions 1 --conditions direct generic full
    python3 -B expriments/RQ2/run.py generate --run expriments/RQ2/runs/mqtt_dgf_001 --workers 3
    python3 -B expriments/RQ2/run.py evaluate --run expriments/RQ2/runs/mqtt_dgf_001
    python3 -B expriments/RQ2/run.py summarize --run expriments/RQ2/runs/mqtt_dgf_001

The selected conditions are frozen in experiment.json and omitted conditions
are absent from the schedule, results and contrasts.

The equivalent CoAP pilot uses the same model, budgets, initial-draft policy,
Generic conversion and three parallel workers:

    python3 -B expriments/RQ2/run.py prepare --out expriments/RQ2/runs/coap_dgf_001 --protocol coap --repetitions 1 --conditions direct generic full
    python3 -B expriments/RQ2/run.py generate --run expriments/RQ2/runs/coap_dgf_001 --workers 3
    python3 -B expriments/RQ2/run.py evaluate --run expriments/RQ2/runs/coap_dgf_001
    python3 -B expriments/RQ2/run.py summarize --run expriments/RQ2/runs/coap_dgf_001

CoAP uses `./coap_server <port>` and the existing 10-scenario CoAP evaluator.
Both initial and final snapshots are evaluated in normal and sanitizer modes.

All inputs, suite sources and evaluator bytes are frozen at preparation.
Changing them requires a new experiment directory. Reinvoking generate after
an interruption runs only pending trials; interrupted and failed trials are
retained and are not replaced. Completed generation cannot be replayed.
Evaluation freezes generation permanently. An interrupted evaluation is
recorded as incomplete rather than rerun. Summaries can be regenerated and
preserve human annotation fields.

## Conditions and isolation

- Direct receives the original TASK, REQUIREMENTS and protocol document,
  with frozen pdftotext extraction for MQTT or RFC text chunks for CoAP.
  Only the TASK sentence requiring public types, interfaces,
  ownership and processing paths to be planned before implementation is removed.
  The private-helper sentence and all functional requirements remain.
- Full receives exactly the published bundle and immutable ABI headers.
- Generic uses deterministic text contracts with RELY, GUARANTEE and
  SPECIFICATION sections, the same source design and ABI, and literal retained
  behavior. It removes WIRE_MAPPING, CALL_CONTRACTS, TEST_VECTORS, source/trace
  metadata and the traceability artifact. No rewriting model is used.
- Full−Vectors removes TEST_VECTORS at every JSON level.
- Full−Contracts removes WIRE_MAPPING and CALL_CONTRACTS at every JSON level.
  Both ablations clear references to removed fields and refresh the publication
  inventory; the removal and reference cleanup are recorded.

Spec conditions cannot read the original inputs. Direct cannot read any Spec.
The model tool sandbox sees only its own inputs, project, command logs and
development reports; controller metadata, historical implementations,
reviewer scripts, API credentials and independent evaluators are not mounted.
Only published artifacts are extracted from historical runs.

The common Coder prompt changes only input navigation. The neutral delivery
manifest has schema_version, files and tests; each test has id, path and
requirement_ids. It does not have spec_test_refs. Development checks require
R01–R12 coverage, a multi-file C project, README and Makefile, and unchanged
planned ABI/sources where supplied. All conditions use the existing normal and
ASan/UBSan build/self-test gate through the optional verifier parameters.

Specification gaps end the trial. No human edits, planner revision, additional
generation or independent-evaluator feedback is used to improve its result.

The first complete project is now measured before execution or code revision.
During the initial draft, Coder uses the same native agent loop with read,
search, list and write tools. Commands and edit_file are unavailable; existing
implementation, test and Makefile files cannot be overwritten. Progress and
delivery metadata may be updated. Once the neutral delivery inventory is
complete, the controller freezes `trials/<trial>/initial_project` with hashes,
response count and elapsed time before its first development build. Incomplete
checkpoints only report inventory readiness, never build or test partial code.
Normal commands, edits, self-tests and bounded repairs then become available.
If the initial job ends without a complete draft, the attempt is retained as
initial_incomplete and its available final artifacts are still evaluated.
The overall 180-response initial job budget includes drafting and subsequent
development repairs; it is not reset at the snapshot boundary.

This initial-draft policy changes the workflow from the earlier pilot, which
allowed execution and modifications while the project was still being written.
Do not combine their first-generation measurements as if the policies matched.

## Results and cost

After all generation ends, both the frozen initial project and final delivered
project are independently evaluated once in normal and sanitizer mode on all
16 frozen MQTT scenarios or 10 frozen CoAP scenarios. The same evaluator bytes and startup metadata apply
to both. Initial results are never mounted in Coder's view and cannot trigger
repairs. Failed generation is still
evaluated when its saved project can build. Missing, duplicated, skipped or
incomplete scenario reports cannot pass. Original projects are hashed before
and after evaluation and are never edited by it.

The primary endpoints are initial build success, initial independent behavior
success, repair operations, final independent success and total tokens. The
denominator is the configured number of generation attempts, not the number
of scenarios or project snapshots. Initial failures and non-execution remain
visible. Build, developer gate, per-scenario outcomes, first gate-observed
successful build, repairs, context resets,
tool use, token usage, latency and wall time are reported separately.
Pending evaluation is labeled incomplete, not claimed as finished evidence.

Repair operations are successful changes to initial delivered source, test or
Makefile bytes after freezing, observed around edit_file, write_file and shell
commands. One tool operation counts once even if it changes multiple files;
source/test/build categories can overlap. Documentation, compiled outputs and
initial file creation are excluded. Responses containing a repair are also
counted. These are revision events, not verified distinct semantic defects or
extra repair jobs. The initial development gate is separately recorded; its
test results are not independent behavior acceptance.

Summary outputs:

- results.json and REPORT.md: conditions, descriptive contrasts and limitations.
- trials.csv, scenarios.csv, stages.csv: attempts, initial/final versions,
  phases, repair breakdowns, status and errors. trials.csv includes initial
  snapshot responses/time, build outcomes and behavior execution status.
- costs.csv: new preparation/generation/evaluation, shared historical formation
  and modeled per-project source reuse at one and three reuses.
- spec_access.csv and evidence.jsonl: accesses, repair file hashes and links to native logs,
  transformation records and developer gate errors. Read requests carry an
  execution status; truncated tool calls are not recorded as executed reads.
- attribution.csv: human-reviewed semantic root causes. Allowed categories are
  protocol_behavior, wire_and_framing, state_and_io, interfaces_and_calls,
  resource_lifecycle and spec_contradiction. Fill evidence and source references;
  set review_status to verified only after actual inspection.

Total tokens are input plus output. Cached input and reasoning are subsets,
not extra charges. Missing usage remains unknown. A job ending in an API
exception has unknown total tokens and model wait time; its native logs retain
the usage of any completed responses. Completed model responses,
logged API errors and queue errors are reported separately; native SDK retries
can include unreported transport attempts. Wall time is not summed model
latency. Per-view preparation timings are detail rows already included in the
overall preparation time; do not add them again.

Historical source formation includes Facts, Design, Specs and initial review.
For the repaired MQTT source it also includes the code attempt that triggered
the gap, Spec repair and renewed review.
The subsequent implementation repairs are excluded. It is one shared historical
cost, not additional expenditure for every condition. Full formation wall time
is unavailable as a separate original record and remains null. Monetary prices
are not estimated.

## Interpretation and validation

Full versus Direct measures the total intermediate-representation contribution,
including planning and public interfaces. Full versus Generic measures the
joint content/representation difference. The two ablations measure conditional
contributions of explicit fields, not removal of all duplicated protocol
semantics. Root causes require human inspection of saved evidence.

Each experiment is limited to one frozen Spec and the selected one or three
generations per condition. MQTT uses a previously repaired Spec; CoAP uses an
unrepaired reviewed Spec. These experiments do not establish upstream Spec
generation stability, pure representation superiority, performance or broad
cross-protocol generalization. Scenarios are not independent experimental samples.

Run development checks without a model credential:

    python3 -B -m unittest discover -s tests -v
    python3 -B -m unittest discover -s expriments/RQ2/tests -v

RQ2 tests use temporary fixtures, mocks and local compiler/sandbox checks.
They never invoke a real model or launch the 15-generation formal experiment.
