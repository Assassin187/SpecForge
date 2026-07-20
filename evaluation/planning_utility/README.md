# Planning Utility Evaluation

This directory implements the downstream utility evaluation for the planning agent. Starting from frozen MQTT minimum protocol facts, it compares:

- `fs-direct-coder`: generates code directly from a facts-derived context.
- `nl-plan-code`: generates a natural-language engineering plan before code.
- `one-shot-structured-planning`: performs structured planning in one model call.
- `full-specforge`: uses the complete staged planning agent.

The experiment measures whether implementation-oriented protocol specs improve downstream compilation and protocol behavior. It does not evaluate facts-agent extraction accuracy.

## Usage

```bash
export ALI_API=<your-api-key>

python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --output-root evaluation/planning_utility/out

python3 -m evaluation.planning_utility.run_matrix \
  --protocol mqtt \
  --method full-specforge \
  --full-planning-dir mqtt=/path/to/planning/run
```

`--max-repair-rounds` controls coder repair for M2/M3. `--max-repair-calls` controls bounded repair for the direct and natural-language baselines.

## Outputs

Each method stores its allowed inputs and hashes, planning or baseline artifacts, generated project, logs, and `summary.json`. The primary metrics are end-to-end success, required behavior scenario pass rate, and final compile success.

Recalculate metrics from saved runs:

```bash
python3 -m evaluation.planning_utility.recalculate_saved_rq1_metrics \
  --output evaluation/planning_utility/out/recalculated_metrics.json
```

Repair an existing M0/M1 project independently:

```bash
python3 -m evaluation.planning_utility.repair_cli \
  --project-dir /path/to/coder_out/mqtt \
  --method fs-direct-coder \
  --protocol mqtt \
  --binary-name mqtt_broker \
  --argv-contract './mqtt_broker <port>'
```

```bash
python3 -m unittest discover -s evaluation/planning_utility/tests -v
```
