#include "icmp_internal.h"

static size_t build_information(uint8_t type, uint8_t *out, size_t out_cap,
                                uint16_t identifier, uint16_t sequence)
{
    if (out == NULL || out_cap < 8)
        return 0;
    out[0] = type;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    put16(out + 4, identifier);
    put16(out + 6, sequence);
    put16(out + 2, icmp_checksum(out, 8));
    return 8;
}

size_t icmp_build_information_request(uint8_t *out, size_t out_cap,
                                      uint16_t identifier, uint16_t sequence)
{
    return build_information(15, out, out_cap, identifier, sequence);
}

size_t icmp_build_information_reply(uint8_t *out, size_t out_cap,
                                    uint16_t identifier, uint16_t sequence)
{
    return build_information(16, out, out_cap, identifier, sequence);
}
