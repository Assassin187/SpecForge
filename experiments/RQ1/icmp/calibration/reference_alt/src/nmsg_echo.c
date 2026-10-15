#include "nmsg_wire.h"
#include "nmsg.h"

int nmsg_echo_request_write(uint8_t *out, size_t out_cap,
                            uint16_t ident, uint16_t sequence_number,
                            const void *data, size_t data_len, size_t *out_length)
{
    size_t length = 8 + data_len;

    if (out == NULL || out_length == NULL || out_cap < length
            || (data == NULL && data_len != 0))
        return -1;
    out[0] = 8;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    wire16(out + 4, ident);
    wire16(out + 6, sequence_number);
    if (data_len != 0)
        memcpy(out + 8, data, data_len);
    wire16(out + 2, wire_checksum(out, length));
    *out_length = length;
    return 0;
}

int nmsg_echo_reply_from(uint8_t *out, size_t out_cap,
                         const uint8_t *request, size_t request_len, size_t *out_length)
{
    size_t data_len, length;

    if (out == NULL || out_length == NULL || request == NULL || request_len < 8
            || request[0] != 8)
        return -1;
    data_len = request_len - 8;
    length = 8 + data_len;
    if (out_cap < length)
        return -1;
    out[0] = 0;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    memcpy(out + 4, request + 4, 4);
    if (data_len != 0)
        memcpy(out + 8, request + 8, data_len);
    wire16(out + 2, wire_checksum(out, length));
    *out_length = length;
    return 0;
}
