/*
 * buffer.h - growable byte buffer primitive for the MQTT 3.1.1 broker.
 *
 * The buffer is a plain byte container: it has no notion of strings, no
 * implicit NUL terminator and performs no text processing. Every operation
 * is length based so binary payloads (including NUL bytes and empty payloads)
 * are preserved exactly.
 *
 * Ownership:
 *   - The buffer owns 'data' and releases it only in buffer_free().
 *   - buffer_consume() drops leading bytes but keeps the storage allocated.
 *   - buffer_append()/buffer_reserve() leave the buffer contents unchanged
 *     when they fail, so a partially encoded packet can never be observed by
 *     a caller.
 */
#ifndef MQTT_BROKER_BUFFER_H
#define MQTT_BROKER_BUFFER_H

#include <stddef.h>
#include <stdint.h>

struct byte_buf {
    uint8_t *data; /* owned allocation, NULL while empty and unallocated */
    size_t len;    /* number of valid bytes held in data */
    size_t cap;    /* allocated capacity of data in bytes */
};

/* Set the buffer to the empty unallocated state. */
void buffer_init(struct byte_buf *buf);

/* Release owned storage and return to the empty unallocated state. */
void buffer_free(struct byte_buf *buf);

/*
 * Ensure that at least 'extra' bytes can be appended after the current
 * contents. Returns 0 on success and -1 when allocation fails or when the
 * required size would overflow; on -1 the buffer is unchanged.
 */
int buffer_reserve(struct byte_buf *buf, size_t extra);

/*
 * Borrow the append position together with the writable space available at
 * it. The returned pointer is valid until the next buffer_* call on the same
 * buffer, and is NULL (with *avail == 0) when no space is reserved.
 */
uint8_t *buffer_tail(struct byte_buf *buf, size_t *avail);

/* Advance len by n bytes previously written through buffer_tail(). */
void buffer_commit(struct byte_buf *buf, size_t n);

/*
 * Append len bytes from src. Returns 0 on success and -1 on failure; on
 * failure no bytes are appended and previously stored bytes are untouched.
 * src is borrowed and is not retained.
 */
int buffer_append(struct byte_buf *buf, const void *src, size_t len);

/*
 * Drop the first n bytes (n <= len), moving the remaining bytes to the front.
 * Storage is retained; no allocation is performed.
 */
void buffer_consume(struct byte_buf *buf, size_t n);

#endif /* MQTT_BROKER_BUFFER_H */
