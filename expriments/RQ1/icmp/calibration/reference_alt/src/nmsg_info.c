#include "nmsg_wire.h"
#include "nmsg.h"

static size_t write_information(uint8_t type, uint8_t *out, size_t cap,
                                uint16_t ident, uint16_t sequence_number)
{
    if (out == NULL || cap < 8)
        return 0;
    out[0] = type;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    wire16(out + 4, ident);
    wire16(out + 6, sequence_number);
    wire16(out + 2, wire_checksum(out, 8));
    return 8;
}

size_t nmsg_info_request_write(uint8_t *out, size_t cap,
                               uint16_t sequence_number, uint16_t ident)
{
    return write_information(15, out, cap, ident, sequence_number);
}

size_t nmsg_info_reply_write(uint8_t *out, size_t cap,
                             uint16_t sequence_number, uint16_t ident)
{
    return write_information(16, out, cap, ident, sequence_number);
}
