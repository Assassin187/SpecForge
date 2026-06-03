from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.planning.cli import build_parser
from agent.planning.orchestrator import PlanningAgent, normalize_resume_stage, normalize_stop_after_stage


ROOT = Path(__file__).resolve().parents[3]


class PlanningPreflightTests(unittest.TestCase):
    def _target_profile(self, root: Path) -> Path:
        path = root / "target_profile.json"
        path.write_text(
            json.dumps(
                {
                    "target_role": "broker",
                    "language": "C",
                    "runtime": "Linux epoll",
                    "scope": "minimum_v1",
                    "deployment_constraints": {
                        "memory_limit": "low",
                        "persistence": False,
                        "tls_mode": "terminated_upstream",
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def test_validate_writes_manifest_with_hashes(self) -> None:
        facts = ROOT / "agent" / "facts" / "gold_facts" / "mqtt_min" / "protocol_facts.json"
        with tempfile.TemporaryDirectory() as raw_tmp:
            target = self._target_profile(Path(raw_tmp))
            agent = PlanningAgent(facts, target, output_dir=Path(raw_tmp) / "run")
            result = agent.validate()

            self.assertTrue(result.success, [diag.__dict__ for diag in result.diagnostics])
            manifest_path = result.output_dir / "_step_logs" / "000_planning_run_manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "planning_run_manifest/v1")
            self.assertTrue(manifest["inputs"]["facts"]["sha256"])
            self.assertEqual(manifest["compatibility"]["facts_input_format_version"], "protocol_facts/v2alpha1")

    def test_plan_cli_accepts_resume_from_stage(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "plan",
                "--facts",
                "facts.json",
                "--target-profile",
                "target.json",
                "--resume-from-stage",
                "architecture",
            ]
        )
        self.assertEqual(args.resume_from_stage, "architecture")
        for stage in ("5.2a", "5.2b", "5.3_type_data", "5.4a_function_inventory", "5.5a_file_layout", "6"):
            args = parser.parse_args(
                [
                    "plan",
                    "--facts",
                    "facts.json",
                    "--target-profile",
                    "target.json",
                    "--resume-from-stage",
                    stage,
                    "--stop-after-stage",
                    stage,
                ]
            )
            self.assertEqual(args.resume_from_stage, stage)
            self.assertEqual(args.stop_after_stage, stage)
        for old_stage in ("5.2", "5.4", "5.5"):
            with self.assertRaises(SystemExit):
                parser.parse_args(["plan", "--facts", "facts.json", "--target-profile", "target.json", "--resume-from-stage", old_stage])
        with self.assertRaises(SystemExit):
            parser.parse_args(["plan", "--facts", "facts.json", "--target-profile", "target.json", "--resume-from-stage", "bad"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["plan", "--facts", "facts.json", "--target-profile", "target.json", "--resume-from-stage", "7"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["plan", "--facts", "facts.json", "--target-profile", "target.json", "--resume-from-stage", "spec_blueprint"])

    def test_stage_aliases_normalize_to_resume_targets(self) -> None:
        self.assertEqual(normalize_resume_stage("5.2"), "5.2")
        self.assertEqual(normalize_resume_stage("5.2a"), "implementation_plan_5_2a")
        self.assertEqual(normalize_resume_stage("5.2b"), "implementation_plan_5_2b")
        self.assertEqual(normalize_resume_stage("5.3"), "implementation_plan_5_3")
        self.assertEqual(normalize_resume_stage("5.3_type_data"), "implementation_plan_5_3")
        self.assertEqual(normalize_resume_stage("5.4a_function_inventory"), "implementation_plan_5_4a")
        self.assertEqual(normalize_resume_stage("5.5a_file_layout"), "implementation_plan_5_5a")
        self.assertEqual(normalize_resume_stage("6"), "specs_compile")
        self.assertEqual(normalize_stop_after_stage("6"), "specs_compile")


if __name__ == "__main__":
    unittest.main()
