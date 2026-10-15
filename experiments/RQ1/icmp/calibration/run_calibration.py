#!/usr/bin/env python3
"""Calibrate ../icmp_check.py against the reference project and mutants.

For each target the suite is executed in both the normal and the sanitize
phase (mirroring specforge's independent evaluation). The reference project
must pass every scenario in both phases; every mutant must fail in the
phases it is exercised in. Writes report.json under calibration/output/.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE.parent / "icmp_check.py"
REFERENCE = HERE / "reference"
OUTPUT = HERE / "output"

# Each mutant: (name, relative file, exact old text, exact new text, phases, note).
MUTANTS = [
    ("checksum_byte_swapped", "src/icmp_errors.c",
     "    put16(out + 2, icmp_checksum(out, length));",
     "    { uint16_t c = icmp_checksum(out, length);\n"
     "      out[2] = (uint8_t)(c & 0xFFu); out[3] = (uint8_t)(c >> 8); }",
     ("normal",), "checksum written little-endian"),
    ("identifier_host_order", "src/icmp_echo.c",
     "    put16(out + 4, identifier);",
     "    out[4] = (uint8_t)identifier; out[5] = (uint8_t)(identifier >> 8);",
     ("normal",), "identifier serialized in host byte order"),
    ("echo_reply_wrong_type", "src/icmp_echo.c",
     "    return build_echo(0, out, out_cap, identifier, sequence, data, data_len);",
     "    return build_echo(8, out, out_cap, identifier, sequence, data, data_len);",
     ("normal",), "echo reply keeps the request Type"),
    ("echo_checksum_header_only", "src/icmp_echo.c",
     "    put16(out + 2, icmp_checksum(out, length));",
     "    put16(out + 2, icmp_checksum(out, 8));",
     ("normal",), "checksum computed over the header only, skipping data"),
    ("quotation_without_ip_header", "src/icmp_errors.c",
     "    size_t length = 8 + quotation_len;",
     "    size_t length = 8 + (quotation_len > 8 ? 8 : quotation_len);",
     ("normal",), "quotation truncated to eight bytes, dropping the IPv4 header"),
    ("unused_field_nonzero", "src/icmp_errors.c",
     "    static const uint8_t zero[4] = {0, 0, 0, 0};\n\n"
     "    return build_quote_message(3, code, zero, out, out_cap, quotation, quotation_len);",
     "    static const uint8_t zero[4] = {0xFF, 0xFF, 0xFF, 0xFF};\n\n"
     "    return build_quote_message(3, code, zero, out, out_cap, quotation, quotation_len);",
     ("normal",), "destination-unreachable unused field not zero"),
    ("odd_length_pad_appended", "src/icmp_echo.c",
     "    size_t length = 8 + data_len;",
     "    size_t length = 8 + data_len + (data_len & 1);",
     ("normal",), "the zero pad octet is appended to the transmitted message"),
    ("redirect_gateway_ignored", "src/icmp_errors.c",
     "    put32(middle, gateway_address);",
     "    put32(middle, 0); (void)gateway_address;",
     ("normal",), "redirect ignores the caller-supplied gateway address"),
    ("timestamp_reply_drops_originate", "src/icmp_timestamp.c",
     "    return build_timestamp(14, out, out_cap, identifier, sequence,\n"
     "                           originate_timestamp, receive_timestamp, transmit_timestamp);",
     "    return build_timestamp(14, out, out_cap, identifier, sequence,\n"
     "                           0, receive_timestamp, transmit_timestamp); "
     "(void)originate_timestamp;",
     ("normal",), "timestamp reply does not preserve the originate timestamp"),
    ("parameter_pointer_off_by_one", "src/icmp_errors.c",
     "    middle[0] = pointer;",
     "    middle[0] = (uint8_t)(pointer + 1);",
     ("normal",), "parameter-problem pointer off by one"),
    ("capacity_not_reported", "src/icmp_echo.c",
     "    if (out == NULL || out_cap < length || (data == NULL && data_len != 0))\n"
     "        return 0;",
     "    if (out == NULL || (data == NULL && data_len != 0))\n"
     "        return 0;\n    (void)out_cap;",
     ("normal", "sanitize"), "echo constructor writes past the supplied capacity"),
    ("time_exceeded_code_ignored", "src/icmp_errors.c",
     "    return build_quote_message(11, code, zero, out, out_cap, quotation, quotation_len);",
     "    return build_quote_message(11, 0, zero, out, out_cap, quotation, quotation_len); "
     "(void)code;",
     ("normal",), "time exceeded always uses Code 0"),
    ("information_reply_wrong_type", "src/icmp_info.c",
     "    return build_information(16, out, out_cap, identifier, sequence);",
     "    return build_information(15, out, out_cap, identifier, sequence);",
     ("normal",), "information reply uses the request Type"),
]


def run(command, cwd=None):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=300)


def build(project: Path, target: str) -> bool:
    return run(["make", "clean"], project).returncode == 0 \
        and run(["make", target], project).returncode == 0


def evaluate(project: Path, phase: str, out: Path, binary_name: str = "icmp_selftest") -> dict:
    result = run([sys.executable, str(CHECKER), "--binary", str(project / binary_name),
                  "--project", str(project), "--out", str(out),
                  "--reference"], cwd=project)
    report_path = out / "report.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {"passed": False}
    report["checker_exit_code"] = result.returncode
    return report


def make_mutant(name: str, relpath: str, old: str, new: str) -> Path:
    target = OUTPUT / "mutants" / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(REFERENCE, target)
    source = target / relpath
    text = source.read_text()
    assert text.count(old) == 1, f"mutant {name}: patch anchor not unique in {relpath}"
    source.write_text(text.replace(old, new, 1))
    return target


def main() -> int:
    started = time.monotonic()
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "mutants").mkdir(parents=True)
    results = []

    for phase, target in (("normal", "all"), ("sanitize", "sanitize")):
        ok = build(REFERENCE, target)
        report = evaluate(REFERENCE, phase, OUTPUT / "reference" / phase) if ok \
            else {"passed": False}
        expected = True
        passed = report["passed"] and all(s["status"] == "passed"
                                          for s in report.get("scenarios", []))
        results.append({"target": "reference", "phase": phase, "build": ok,
                        "expected_pass": expected, "suite_passed": passed,
                        "verdict_ok": passed == expected,
                        "scenarios": report.get("scenarios", [])})
        print(f"reference/{phase}: suite_passed={passed} (expected {expected})", flush=True)

    # A deliberately different public API style must also pass every scenario.
    alt = HERE / "reference_alt"
    for phase, target in (("normal", "all"), ("sanitize", "sanitize")):
        ok = build(alt, target)
        report = evaluate(alt, phase, OUTPUT / "reference_alt" / phase,
                          binary_name="nmsg_selftest") if ok else {"passed": False}
        passed = report["passed"] and all(s["status"] == "passed"
                                          for s in report.get("scenarios", []))
        results.append({"target": "reference_alt", "phase": phase, "build": ok,
                        "expected_pass": True, "suite_passed": passed,
                        "verdict_ok": passed,
                        "scenarios": report.get("scenarios", [])})
        print(f"reference_alt/{phase}: suite_passed={passed} (expected True)", flush=True)

    for name, relpath, old, new, phases, note in MUTANTS:
        mutant = make_mutant(name, relpath, old, new)
        for phase in phases:
            ok = build(mutant, "sanitize" if phase == "sanitize" else "all")
            report = evaluate(mutant, phase, OUTPUT / "mutants" / name / phase) if ok \
                else {"passed": False}
            failed_ids = [s["id"] for s in report.get("scenarios", [])
                          if s["status"] != "passed"]
            results.append({"target": name, "phase": phase, "build": ok, "note": note,
                            "expected_pass": False, "suite_passed": report["passed"],
                            "verdict_ok": not report["passed"],
                            "failed_scenarios": failed_ids})
            print(f"mutant {name}/{phase}: suite_passed={report['passed']} "
                  f"(expected False); failed={failed_ids}", flush=True)

    verdict = all(r["verdict_ok"] for r in results)
    summary = {"calibration_passed": verdict,
               "targets": len(results),
               "elapsed_seconds": round(time.monotonic() - started, 3),
               "results": results}
    (OUTPUT / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"calibration {'PASSED' if verdict else 'FAILED'} "
          f"({sum(1 for r in results if r['verdict_ok'])}/{len(results)} targets behaved "
          f"as expected)")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
