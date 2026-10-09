[PROMPT]
Perform one nonblocking recv on a peer descriptor, retrying EINTR internally and distinguishing progress, would-block, orderly peer close and fatal error.

[RELY]
STRUCT:

FUNC:
  - NAME:
recv
    KIND:
TYPE_REF
    ROLE:
one nonblocking read of at most cap bytes into the caller's buffer
VAR:
  - NAME:
EINTR
    ROLE:
retry the read after a signal
  - NAME:
EAGAIN
    ROLE:
no data available right now
  - NAME:
EWOULDBLOCK
    ROLE:
same meaning as EAGAIN

[GUARANTEE]
RAW:
enum net_io_result net_read_some(int fd, uint8_t *buf, size_t cap, size_t *out_n)
NAME:
net_read_some
RETURN:
enum net_io_result
PARAMS:
  - TYPE:
int
    NAME:
fd
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
uint8_t *
    NAME:
buf
    NULLABLE:
true
    OWNERSHIP:
BORROWED
  - TYPE:
size_t
    NAME:
cap
    NULLABLE:
false
    OWNERSHIP:
BORROWED
  - TYPE:
size_t *
    NAME:
out_n
    NULLABLE:
true
    OWNERSHIP:
BORROWED

[SPECIFICATION]
FUNCTION_TYPE:
ALGORITHM
LOGIC:
  INPUT:
fd: a nonblocking peer descriptor (set by net_accept). buf/cap: caller-owned writable bytes, normally a slice of buffer_tail of the session input buffer, so cap is the free capacity. out_n: bytes read.
  ACTION:
1) If out_n is NULL return NET_IO_ERROR; *out_n = 0. 2) If fd < 0 or buf is NULL or cap == 0 return NET_IO_ERROR (a zero-capacity read cannot distinguish data from EOF, so it is refused instead of being reported as a close). 3) Loop: n = recv(fd, buf, cap, 0); if n > 0 { *out_n = (size_t)n; return NET_IO_PROGRESS; } if n == 0 return NET_IO_CLOSED (the peer performed an orderly close: every complete frame already buffered must still be processed, which the caller does by calling broker_session_eof rather than discarding the buffer); if n < 0: if errno == EINTR continue; if errno == EAGAIN || errno == EWOULDBLOCK return NET_IO_WOULD_BLOCK; else return NET_IO_ERROR. 4) The call never blocks on a slow or idle peer because the descriptor is nonblocking, so one call transfers at most cap bytes and returns promptly; the read loop in the caller bounds how many times it calls this per readiness event. Nothing is retained or allocated by this function.
  OUTPUT:
NET_IO_PROGRESS with *out_n in 1..cap when bytes were received. NET_IO_WOULD_BLOCK with *out_n == 0 when no more bytes are available now. NET_IO_CLOSED with *out_n == 0 when the peer shut down its write side (TCP FIN) - the caller must still process complete buffered frames (scope R08). NET_IO_ERROR with *out_n == 0 for NULL/invalid arguments, a zero cap or a fatal read error.
  INVARIANTS_USED:
    - the descriptor is nonblocking, so a readiness-driven single read can never stall other peers
    - 0 bytes means peer close, not an error, and never discards buffered data
    - EINTR is retried inside this function so a signal exit is still clean
    - the caller's buffer content is only meaningful for the *out_n bytes reported
  PRECONDITION:
fd is NULL or a valid nonblocking descriptor; buf is NULL or points to cap writable bytes.
  POSTCONDITION:
On progress exactly *out_n bytes at buf were overwritten; nothing else in the buffer changed and no state outside the caller's buffer was modified.
  IDEMPOTENT:
false
  THREAD_SAFETY:
Safe per descriptor; concurrent reads on the same descriptor would interleave.
