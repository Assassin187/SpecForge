from __future__ import annotations

import unittest

from evaluation.spec_ablation.initial_diagnostic_taxonomy import (
    SECTION_END,
    SECTION_START,
    classify_line,
    classify_message,
    replace_marked_section,
)


class InitialDiagnosticTaxonomyTests(unittest.TestCase):
    def test_classifies_local_c_and_portability_diagnostics(self) -> None:
        self.assertEqual(
            classify_message("implicit declaration of function ‘free’ [-Werror=implicit-function-declaration]"),
            "local_c_portability",
        )
        self.assertEqual(classify_message("unknown type name ‘uint8_t’"), "local_c_portability")
        self.assertEqual(classify_message("‘errno’ undeclared (first use in this function)"), "local_c_portability")

    def test_classifies_type_and_field_diagnostics_separately(self) -> None:
        self.assertEqual(classify_message("unknown type name ‘mqtt_session_t’"), "type_visibility_layout")
        self.assertEqual(
            classify_message("request for member ‘fd’ in something not a structure or union"),
            "type_visibility_layout",
        )
        self.assertEqual(
            classify_message("‘mqtt_connect_payload_t’ has no member named ‘will_topic’"),
            "field_schema_hallucination",
        )

    def test_classifies_api_symbol_and_syntax_diagnostics(self) -> None:
        self.assertEqual(classify_message("conflicting types for ‘decode_one’; have ‘int(void)’"), "api_signature_drift")
        self.assertEqual(
            classify_message("implicit declaration of function ‘ensure_cap’ [-Werror=implicit-function-declaration]"),
            "symbol_visibility_hallucination",
        )
        self.assertEqual(
            classify_message("‘MQTT_PACKET_TYPE_CONNECT’ undeclared (first use in this function)"),
            "symbol_visibility_hallucination",
        )
        self.assertEqual(classify_message("expected ‘:’ before ‘}’ token"), "syntax_expression")

    def test_line_parser_ignores_build_tool_summary_lines(self) -> None:
        source = "protocol/mqtt_decoder.c:10:3: error: unknown type name ‘mqtt_packet_t’"
        self.assertEqual(classify_line(source).category, "type_visibility_layout")
        linker = "/usr/bin/ld: broker.o: undefined reference to `mqtt_broker_run'"
        self.assertEqual(classify_line(linker).category, "symbol_visibility_hallucination")
        self.assertIsNone(classify_line("collect2: error: ld returned 1 exit status"))
        self.assertIsNone(classify_line("make: *** [Makefile:20: mqtt_broker] Error 1"))

    def test_marked_summary_update_is_idempotent(self) -> None:
        first = f"{SECTION_START}\nfirst\n{SECTION_END}"
        second = f"{SECTION_START}\nsecond\n{SECTION_END}"
        document = "# Report\n\n" + first + "\n"
        updated = replace_marked_section(document, second)
        self.assertIn("second", updated)
        self.assertNotIn("first", updated)
        self.assertEqual(updated.count(SECTION_START), 1)
        self.assertEqual(updated.count(SECTION_END), 1)


if __name__ == "__main__":
    unittest.main()
