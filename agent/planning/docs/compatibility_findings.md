# Planning Agent Compatibility Findings

This document records the current compatibility boundary used by the new
Planning Agent implementation.

## Facts Agent Input

Planning accepts the current Facts Agent output format:

- `schema_version`: `protocol_facts/v2alpha1`
- required semantic sections:
  - `protocol_meta`
  - `transport`
  - `interaction_model`
  - `message_model`
  - `state_model`
  - `routing_model`
  - `resource_model`
  - `error_and_limits`
  - `minimum_v1`
  - `open_questions`
  - `evidence_index`

`planning_inputs` is useful when present, but older fixtures do not always
contain it, so treats it as optional.

Current facts generally carry `evidence_refs` but not `fact_id`. The facts
adapter therefore creates stable path-derived IDs such as
`fact:transport_network_stack` and records the mapping in
`planning_ir.normalization_index`.

## Target Profile Input

Planning accepts the current target profile shape:

- `target_role`
- `language`
- `runtime`
- `scope`
- `deployment_constraints`
- optional `role_aliases`

The adapter stores these values under `target_directives`; target information
must not be mixed into `planning_ir.protocol_facts`.

## Coder Agent Output Boundary

The current Coder Agent consumes a `spec_bundle/` directory and discovers one
`*_module_spec.json` file. It recursively reads `PROTOCOL_MODULE_SPEC`,
`FILE_SPEC`, and `FUNCTION_SPEC` JSON files.

Planning has not changed the Coder loader. Future compiler stages must emit
the existing `spec_bundle/` format and may write `coder_manifest.json` outside
that directory as a non-consumed index.
