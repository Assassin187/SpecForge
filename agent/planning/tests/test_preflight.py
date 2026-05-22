from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.planning.cli import build_parser
from agent.planning.orchestrator import PlanningAgent


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
        args = parser.parse_args(
            [
                "plan",
                "--facts",
                "facts.json",
                "--target-profile",
                "target.json",
                "--resume-from-stage",
                "5.4d",
                "--stop-after-stage",
                "architecture",
            ]
        )
        self.assertEqual(args.resume_from_stage, "5.4d")
        self.assertEqual(args.stop_after_stage, "architecture")
        with self.assertRaises(SystemExit):
            parser.parse_args(["plan", "--facts", "facts.json", "--target-profile", "target.json", "--resume-from-stage", "bad"])


if __name__ == "__main__":
    unittest.main()
