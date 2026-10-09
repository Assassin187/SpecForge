[PROMPT]
Parse the <port> command line argument into a 1..65535 TCP port: decimal digits only, no whitespace, no sign, no trailing characters, overflow checked.

[RELY]
STRUCT:

FUNC:

VAR:


[GUARANTEE]
RAW:
int net_parse_port(const char *text, uint16_t *out_port)
NAME:
net_parse_port
RETURN:
int
PARAMS:
  - TYPE:
const char *
    NAME:
text
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
uint16_t *
    NAME:
out_port
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
text: the argv[1] string of ./mqtt_broker as a NUL-terminated C string. out_port: receives the parsed port only on success.
  ACTION:
1) If text is NULL or out_port is NULL return -1. 2) If text[0] == '\0' return -1 (an empty argument has no digits). 3) value = 0; for (p = text; *p != '\0'; p++): if *p < '0' or *p > '9' return -1 (this rejects '+', '-', spaces, tabs, newlines, '0x' prefixes, signs and any trailing character such as '1883abc', and it also rejects a bare space in front of the digits because the loop starts at text[0] and checks every byte); digit = *p - '0'; if value > 6553 return -1 (65535 / 10 = 6553 leaves 5, so a value above 6553 cannot be extended by any digit without exceeding 65535); value = value * 10 + digit; if value > 65535 return -1. 4) If value == 0 return -1 (port 0 is not a usable service port for the command line; the library level net_listen still accepts 0 for tests). 5) *out_port = (uint16_t)value; return 0. No errno is used or set; the result is a pure function of text.
  OUTPUT:
0 with *out_port in 1..65535 when text is a pure decimal representation of a value in that range (leading zeros are legal because they are decimal digits: "01883" parses to 1883, "0" and "000" are rejected as value 0). -1 for NULL arguments, an empty string, any non-digit byte anywhere in the string (including whitespace, signs and trailing characters) or a value outside 1..65535; *out_port is left untouched in that case. main must print a usage message and exit non-zero when this returns -1 (scope R01).
  INVARIANTS_USED:
    - the CLI takes exactly one port argument
    - only decimal digits form a valid port argument (no whitespace, no sign, no trailing characters)
    - the accepted range is 1..65535
  PRECONDITION:
text is NULL or points to a NUL-terminated string.
  POSTCONDITION:
on success *out_port is the exact numeric value of text; on failure *out_port is unchanged.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments.
