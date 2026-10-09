#include "icmp_internal.h"

static size_t build_echo(uint8_t type, uint8_t *out, size_t out_cap,
                         uint16_t identifier, uint16_t sequence,
                         const void *data, size_t data_len)
{
    size_t length = 8 + data_len;

    if (out == NULL || out_cap < length || (data == NULL && data_len != 0))
        return 0;
    out[0] = type;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    put16(out + 4, identifier);
    put16(out + 6, sequence);
    if (data_len != 0)
        memcpy(out + 8, data, data_len);
    put16(out + 2, icmp_checksum(out, length));
    return length;
}

size_t icmp_build_echo_request(uint8_t *out, size_t out_cap,
                               uint16_t identifier, uint16_t sequence,
                               const void *data, size_t data_len)
{
    return build_echo(8, out, out_cap, identifier, sequence, data, data_len);
}

size_t icmp_build_echo_reply(uint8_t *out, size_t out_cap,
                             uint16_t identifier, uint16_t sequence,
                             const void *data, size_t data_len)
{
    return build_echo(0, out, out_cap, identifier, sequence, data, data_len);
}
