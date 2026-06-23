from __future__ import annotations

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

from agent.coder.protocol_behavior_val import smtp
from agent.planning.adapters.target_profile import load_target_profile


ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = ROOT / "tools" / "eval" / "run_minimum_matrix.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_minimum_matrix", RUNNER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MinimumMatrixRunnerTests(unittest.TestCase):
    def test_runner_uses_existing_gold_facts(self) -> None:
        runner = _load_runner()

        self.assertEqual(
            runner.PROTOCOLS["smtp"].facts_path,
            ROOT / "agent" / "facts" / "gold_facts" / "smtp_min" / "protocol_facts.json",
        )
        for config in runner.PROTOCOLS.values():
            self.assertTrue(config.facts_path.is_file(), config.facts_path)

    def test_new_target_profiles_load(self) -> None:
        for path in (
            ROOT / "agent" / "planning" / "planning_target_profile_coap.json",
            ROOT / "agent" / "planning" / "planning_target_profile_smtp_min.json",
        ):
            profile, diagnostics = load_target_profile(path)
            errors = [diag for diag in diagnostics if diag.level == "error"]
            self.assertIsNotNone(profile)
            self.assertFalse(errors)

    def test_smtp_smoke_matches_auth_required_facts(self) -> None:
        facts_path = ROOT / "agent" / "facts" / "gold_facts" / "smtp_min" / "protocol_facts.json"
        facts_text = facts_path.read_text(encoding="utf-8").upper()
        self.assertIn("AUTH", facts_text)

        run_source = inspect.getsource(smtp.run)
        self.assertIn("_smtp_auth_login(sock)", run_source)


if __name__ == "__main__":
    unittest.main()
