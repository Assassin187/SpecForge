#include "nmsg_wire.h"
#include "nmsg.h"

static size_t finish(uint8_t type, uint8_t code, const uint8_t middle[4],
                     const uint8_t *quote, size_t quote_len, uint8_t *out, size_t cap)
{
    size_t length = 8 + quote_len;

    if (out == NULL || quote == NULL || cap < length)
        return 0;
    out[0] = type;
    out[1] = code;
    out[2] = 0;
    out[3] = 0;
    memcpy(out + 4, middle, 4);
    memcpy(out + 8, quote, quote_len);
    wire16(out + 2, wire_checksum(out, length));
    return length;
}

size_t nmsg_unreach_write(uint8_t *out, size_t out_cap, const nmsg_unreach_t *cfg)
{
    static const uint8_t zero[4] = {0, 0, 0, 0};

    if (cfg == NULL)
        return 0;
    return finish(3, cfg->code, zero, cfg->quote, cfg->quote_len, out, out_cap);
}

size_t nmsg_quench_write(uint8_t *out, size_t cap, const uint8_t *quote, size_t quote_len)
{
    static const uint8_t zero[4] = {0, 0, 0, 0};

    return finish(4, 0, zero, quote, quote_len, out, cap);
}

size_t nmsg_time_exceeded_write(uint8_t *out, size_t cap, const uint8_t *quote,
                                size_t quote_len, uint8_t code)
{
    static const uint8_t zero[4] = {0, 0, 0, 0};

    return finish(11, code, zero, quote, quote_len, out, cap);
}

size_t nmsg_param_problem_write(uint8_t *out, size_t cap, uint8_t pointer,
                                const uint8_t *quote, size_t quote_len)
{
    uint8_t middle[4] = {0, 0, 0, 0};

    middle[0] = pointer;
    return finish(12, 0, middle, quote, quote_len, out, cap);
}

size_t nmsg_redirect_write(uint8_t *out, size_t cap, const uint8_t *quote,
                           size_t quote_len, uint32_t gateway, uint8_t code)
{
    uint8_t middle[4];

    wire32(middle, gateway);
    return finish(5, code, middle, quote, quote_len, out, cap);
}
