#include "icmp_internal.h"

static size_t build_timestamp(uint8_t type, uint8_t *out, size_t out_cap,
                              uint16_t identifier, uint16_t sequence,
                              uint32_t originate, uint32_t receive, uint32_t transmit)
{
    if (out == NULL || out_cap < 20)
        return 0;
    out[0] = type;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    put16(out + 4, identifier);
    put16(out + 6, sequence);
    put32(out + 8, originate);
    put32(out + 12, receive);
    put32(out + 16, transmit);
    put16(out + 2, icmp_checksum(out, 20));
    return 20;
}

size_t icmp_build_timestamp_request(uint8_t *out, size_t out_cap,
                                    uint16_t identifier, uint16_t sequence,
                                    uint32_t originate_timestamp)
{
    return build_timestamp(13, out, out_cap, identifier, sequence,
                           originate_timestamp, 0, 0);
}

size_t icmp_build_timestamp_reply(uint8_t *out, size_t out_cap,
                                  uint16_t identifier, uint16_t sequence,
                                  uint32_t originate_timestamp,
                                  uint32_t receive_timestamp,
                                  uint32_t transmit_timestamp)
{
    return build_timestamp(14, out, out_cap, identifier, sequence,
                           originate_timestamp, receive_timestamp, transmit_timestamp);
}
