# Reference assets

`mqtt_reference/original_specs` preserves the original manual MQTT Specs.
`mqtt_reference/bundle` is the normalized manual-reference fixture with public
headers only. No reference C implementation is copied. `migration.json` records
original hashes and normalization changes. Empty original provenance remains
empty; this fixture does not claim automatic document grounding.

`mqtt_reference_facts` contains historical gold facts and coverage rubrics for
offline analysis. Fresh model stages never mount these assets. A successful
manual bundle coding run is recorded as `spec_only`, not as automatic planning.

The six legacy behavior names come from the old
`agent/coder/protocol_behavior_val/mqtt.py`; the new harness checks exact packets.
The original Kimi TASK/REQUIREMENTS request a wider subset. The new minimum
case is written explicitly rather than silently reducing those inputs.

