[PROMPT]
Append the 7-bits-per-byte Remaining Length encoding of value (private).

[RELY]
STRUCT:
  - NAME:
struct byte_buf
    ROLE:
destination buffer whose cap must already cover the encoded field
FUNC:
  - NAME:
buffer_append
    KIND:
CALL
    ROLE:
append each encoded byte after the capacity reservation
VAR:
  - NAME:
WIRE_MAX_REMAINING_LENGTH
    ROLE:
268435455 is the largest encodable value

[GUARANTEE]
RAW:
static int wire_write_remaining_length(struct byte_buf *out, size_t value)
NAME:
wire_write_remaining_length
RETURN:
int
PARAMS:
  - TYPE:
struct byte_buf *
    NAME:
out
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
value
    NULLABLE:
false
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
out (the packet buffer being built) and value in 0..268435455.
  ACTION:
1) If out is NULL return -1. 2) If value > 268435455 return -1 (the encoding cannot express it; a longer field is malformed, fact F004). 3) Encode into a 4-byte stack array: repeated do { b = value % 128; value /= 128; if (value > 0) b |= 0x80; field[i++] = b; } while (value > 0) so the low 7 bits come first and the continuation bit 0x80 marks 'more bytes follow'. 4) Append the i bytes in order with buffer_append (i is 1..4); buffer_append itself guarantees all-or-nothing behaviour, so an allocation failure returns -1 with the output unchanged. 5) Return 0. Encoders call this after reserving the whole packet size, so the append cannot reallocate; the worst case is therefore reported as a generic failure.
  OUTPUT:
0 when the 1..4 byte field was appended; the buffer length grew by exactly i. -1 when out is NULL, value exceeds 268435455, or the append failed; in every -1 case no byte was appended.
  INVARIANTS_USED:
    - the low 7 bits of the value are transmitted first
    - the high bit of every byte except the last is 1
    - standard encoding never uses more than 4 bytes for a legal value
  PRECONDITION:
out is NULL or a valid buffer.
  POSTCONDITION:
On success buffer[len-i .. len-1] is the field and the rest of the buffer is untouched.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Not thread safe; the caller owns the buffer.
