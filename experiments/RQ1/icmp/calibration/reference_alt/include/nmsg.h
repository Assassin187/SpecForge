#ifndef NMSG_H
#define NMSG_H

#include <stddef.h>
#include <stdint.h>

/*
 * Deliberately different public API style for calibration: status returns
 * with out-length parameters, reply-from-request constructors, a
 * fill+serialize split for timestamps, and a config struct for Destination
 * Unreachable. All constructors serialize complete RFC 792 wire messages
 * starting at the Type field.
 *
 * Integer-returning constructors return 0 on success and -1 on failure
 * (missing input or insufficient output capacity) and store the serialized
 * length through `out_length`.
 */

typedef struct {
    uint8_t code;
    const uint8_t *quote;
    size_t quote_len;
} nmsg_unreach_t;

typedef struct {
    uint8_t type;
    uint16_t ident;
    uint16_t seq;
    uint32_t originate;
    uint32_t receive;
    uint32_t transmit;
} nmsg_timestamp_t;

int nmsg_echo_request_write(uint8_t *out, size_t out_cap,
                            uint16_t ident, uint16_t sequence_number,
                            const void *data, size_t data_len, size_t *out_length);
int nmsg_echo_reply_from(uint8_t *out, size_t out_cap,
                         const uint8_t *request, size_t request_len, size_t *out_length);

size_t nmsg_unreach_write(uint8_t *out, size_t out_cap, const nmsg_unreach_t *cfg);
size_t nmsg_quench_write(uint8_t *out, size_t cap, const uint8_t *quote, size_t quote_len);
size_t nmsg_time_exceeded_write(uint8_t *out, size_t cap, const uint8_t *quote,
                                size_t quote_len, uint8_t code);
size_t nmsg_param_problem_write(uint8_t *out, size_t cap, uint8_t pointer,
                                const uint8_t *quote, size_t quote_len);
size_t nmsg_redirect_write(uint8_t *out, size_t cap, const uint8_t *quote,
                           size_t quote_len, uint32_t gateway, uint8_t code);

void nmsg_fill_timestamp_request(nmsg_timestamp_t *msg, uint16_t ident, uint16_t seq,
                                 uint32_t originate);
void nmsg_fill_timestamp_reply(nmsg_timestamp_t *msg, uint16_t ident, uint16_t seq,
                               uint32_t originate, uint32_t receive, uint32_t transmit);
size_t nmsg_timestamp_serialize(uint8_t *out, size_t cap, const nmsg_timestamp_t *msg);

size_t nmsg_info_request_write(uint8_t *out, size_t cap,
                               uint16_t sequence_number, uint16_t ident);
size_t nmsg_info_reply_write(uint8_t *out, size_t cap,
                             uint16_t sequence_number, uint16_t ident);

#endif
