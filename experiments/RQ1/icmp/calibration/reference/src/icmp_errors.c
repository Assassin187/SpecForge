#include "icmp_internal.h"

static size_t build_quote_message(uint8_t type, uint8_t code, const uint8_t middle[4],
                                  uint8_t *out, size_t out_cap,
                                  const uint8_t *quotation, size_t quotation_len)
{
    size_t length = 8 + quotation_len;

    if (out == NULL || out_cap < length || quotation == NULL)
        return 0;
    out[0] = type;
    out[1] = code;
    out[2] = 0;
    out[3] = 0;
    memcpy(out + 4, middle, 4);
    memcpy(out + 8, quotation, quotation_len);
    put16(out + 2, icmp_checksum(out, length));
    return length;
}

size_t icmp_build_destination_unreachable(uint8_t *out, size_t out_cap, uint8_t code,
                                          const uint8_t *quotation, size_t quotation_len)
{
    static const uint8_t zero[4] = {0, 0, 0, 0};

    return build_quote_message(3, code, zero, out, out_cap, quotation, quotation_len);
}

size_t icmp_build_source_quench(uint8_t *out, size_t out_cap,
                                const uint8_t *quotation, size_t quotation_len)
{
    static const uint8_t zero[4] = {0, 0, 0, 0};

    return build_quote_message(4, 0, zero, out, out_cap, quotation, quotation_len);
}

size_t icmp_build_redirect(uint8_t *out, size_t out_cap, uint8_t code,
                           uint32_t gateway_address,
                           const uint8_t *quotation, size_t quotation_len)
{
    uint8_t middle[4];

    put32(middle, gateway_address);
    return build_quote_message(5, code, middle, out, out_cap, quotation, quotation_len);
}

size_t icmp_build_time_exceeded(uint8_t *out, size_t out_cap, uint8_t code,
                                const uint8_t *quotation, size_t quotation_len)
{
    static const uint8_t zero[4] = {0, 0, 0, 0};

    return build_quote_message(11, code, zero, out, out_cap, quotation, quotation_len);
}

size_t icmp_build_parameter_problem(uint8_t *out, size_t out_cap, uint8_t pointer,
                                    const uint8_t *quotation, size_t quotation_len)
{
    uint8_t middle[4] = {0, 0, 0, 0};

    middle[0] = pointer;
    return build_quote_message(12, 0, middle, out, out_cap, quotation, quotation_len);
}
