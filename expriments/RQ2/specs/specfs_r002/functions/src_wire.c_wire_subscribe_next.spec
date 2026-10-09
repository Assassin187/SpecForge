[PROMPT]
Read the index-th filter/QoS pair out of an already validated SUBSCRIBE payload, in payload order, without allocating.

[RELY]
STRUCT:
  - NAME:
struct wire_subscribe
    ROLE:
validated payload slice to walk
  - NAME:
struct wire_span
    ROLE:
borrowed filter slice returned to the caller
FUNC:
  - NAME:
wire_read_u16
    KIND:
CALL
    ROLE:
read each filter length prefix while walking
VAR:


[GUARANTEE]
RAW:
int wire_subscribe_next(const struct wire_subscribe *sub, size_t index, struct wire_span *filter, uint8_t *qos)
NAME:
wire_subscribe_next
RETURN:
int
PARAMS:
  - TYPE:
const struct wire_subscribe *
    NAME:
sub
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
index
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
struct wire_span *
    NAME:
filter
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t *
    NAME:
qos
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
sub: a struct wire_subscribe already produced by wire_decode_subscribe, whose payload/payload_len describe the validated filter/QoS payload. index: zero-based pair position. filter and qos: outputs.
  ACTION:
1) If sub is NULL, filter is NULL or qos is NULL return 0. 2) pos = 0; i = 0. 3) Loop: while (pos < sub->payload_len): (a) if pos + 2 > sub->payload_len return 0 (no such pair); flen = wire_read_u16(sub->payload + pos); pos += 2; (b) if flen == 0 or pos + flen + 1 > sub->payload_len return 0 (defensive: a validated payload cannot take this path, but the walk must never read past payload_len); (c) if i == index: filter->data = sub->payload + pos; filter->len = flen; *qos = sub->payload[pos + flen]; return 1; (d) pos += flen + 1; i++. 4) Return 0 (index is past the last pair). Nothing is written to *filter or *qos on the 0 path. No allocation; the returned span borrows sub->payload, i.e. the wire packet buffer, and lives until that buffer is reused or freed.
  OUTPUT:
1 with *filter = a borrowed slice of exactly the filter bytes (no length prefix, no QoS byte) and *qos = the decoded requested QoS value 0, 1 or 2, both taken from the index-th pair in payload order. 0 when index has no pair; *filter and *qos are left untouched in that case.
  INVARIANTS_USED:
    - the payload is a sequence of (2-byte length, filter bytes, 1 QoS byte) triples
    - pair order in the payload is the order used in the SUBACK result list
    - the returned slice borrows the caller's packet buffer
  PRECONDITION:
sub was produced by a successful wire_decode_subscribe call on a still-live packet buffer.
  POSTCONDITION:
on success the outputs describe the index-th pair; the payload buffer is not modified.
  IDEMPOTENT:
true
  THREAD_SAFETY:
Pure function of its arguments; the payload buffer must not be mutated concurrently.
