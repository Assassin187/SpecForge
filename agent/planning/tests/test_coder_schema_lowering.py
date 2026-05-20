from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.planning.adapters.facts_input import build_planning_ir
from agent.planning.adapters.target_profile import load_target_profile
from agent.planning.stages.architecture import build_architecture_candidates, select_architecture
from agent.planning.stages.blueprint import build_spec_blueprint
from agent.planning.stages.constraints import activate_constraints
from agent.planning.stages.coder_spec_lowering import (
    normalize_data_visibility_for_coder,
    normalize_interface_visibility_for_coder,
    normalize_param_ownership_for_coder,
)
from agent.planning.stages.implementation_plan import build_implementation_plan
from agent.planning.stages.protocol_profile import build_protocol_profile
from agent.planning.stages.specs_compiler import compile_spec_bundle
from agent.planning.validators.coder_schema import validate_coder_spec_bundle_against_schema


ROOT = Path(__file__).resolve().parents[3]


def _target_profile(root: Path) -> Path:
    path = root / "target_profile.json"
    path.write_text(
        json.dumps(
            {
                "target_role": "broker",
                "language": "C",
                "runtime": "Linux epoll",
                "scope": "minimum_v1",
                "deployment_constraints": {"memory_limit": "low", "persistence": False, "tls_mode": "terminated_upstream"},
            }
        ),
        encoding="utf-8",
    )
    return path


class CoderSchemaLoweringTests(unittest.TestCase):
    def test_existing_example_specs_are_schema_valid(self) -> None:
        diagnostics = validate_coder_spec_bundle_against_schema(ROOT / "specs-example" / "mqtt_specs")
        self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

    def test_normalizers_match_coder_schema_enums(self) -> None:
        self.assertEqual(normalize_param_ownership_for_coder("borrowed"), "BORROWED")
        self.assertEqual(normalize_param_ownership_for_coder("value"), "UNKNOWN")
        self.assertEqual(normalize_param_ownership_for_coder("transferred"), "TRANSFER")
        self.assertEqual(normalize_interface_visibility_for_coder("internal"), "private")
        self.assertEqual(normalize_data_visibility_for_coder("internal"), "PRIVATE")

    def test_compiled_planning_bundle_is_strict_schema_valid(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            target, target_diags = load_target_profile(_target_profile(tmp))
            self.assertIsNotNone(target, [diag.__dict__ for diag in target_diags])
            planning_ir, ir_diags = build_planning_ir(facts, target)
            self.assertIsNotNone(planning_ir, [diag.__dict__ for diag in ir_diags])
            profile = build_protocol_profile(planning_ir)
            constraints = activate_constraints(profile)
            candidates = build_architecture_candidates(planning_ir, profile, constraints)
            selected = select_architecture(candidates, profile)
            plan = build_implementation_plan(planning_ir, profile, constraints, selected)
            blueprint = build_spec_blueprint(plan)
            manifest, _ = compile_spec_bundle(blueprint, tmp)
            diagnostics = validate_coder_spec_bundle_against_schema(manifest["spec_root"])
            self.assertFalse([diag.__dict__ for diag in diagnostics if diag.level == "error"])

            for path in Path(manifest["spec_root"]).rglob("*_spec.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("TRACEABILITY", raw)
                if raw.get("KIND") == "FUNCTION_SPEC":
                    self.assertNotIn("CAPABILITY_IDS", raw)
                    self.assertNotIn("STATE_ACCESS", raw)
                    self.assertNotIn("CALLS_ALLOWED", raw)


if __name__ == "__main__":
    unittest.main()
