# Spec Form Ablation

This directory studies how coder-facing specification forms affect generation quality. All four treatments derive from the same `specs-example/<protocol>_specs` source:

- S1: local function specs and raw headers.
- S2: S1 plus the global project graph.
- S3: S2 plus structured type and interface grounding.
- S4: complete SpecForge protocol specs.

`transformer.py` deterministically projects S1-S3, which are consumed by `view_generator.py`. S4 uses the coder directly. This experiment does not evaluate facts extraction or planning quality.

## Usage

```bash
python3 -m evaluation.spec_ablation.transformer \
  --protocol all \
  --view all \
  --output evaluation/spec_ablation/specs \
  --overwrite

python3 -m evaluation.spec_ablation.view_generator \
  --view s2 \
  --view-root evaluation/spec_ablation/specs/mqtt/s2 \
  validate
```

Run the generation experiment:

```bash
export ALI_API=<your-api-key>
evaluation/spec_ablation/run_baseline_generation.sh \
  --protocol mqtt \
  --view all \
  --start-round 1 \
  --rounds 3
```

Generate a single S1-S3 run:

```bash
python3 -m evaluation.spec_ablation.view_generator \
  --view s3 \
  --view-root evaluation/spec_ablation/specs/mqtt/s3 \
  --output-dir evaluation/spec_ablation/out/mqtt_s3 \
  generate
```

Results are written to `evaluation/spec_ablation/out/`. The main reported outcomes are end-to-end success, behavior pass rate, compile/repair status, failure taxonomy, and token usage.

```bash
python3 -m unittest discover -s evaluation/spec_ablation/tests -v
```
