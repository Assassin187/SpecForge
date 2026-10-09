#include "nmsg_wire.h"
#include "nmsg.h"

void nmsg_fill_timestamp_request(nmsg_timestamp_t *msg, uint16_t ident, uint16_t seq,
                                 uint32_t originate)
{
    if (msg == NULL)
        return;
    msg->type = 13;
    msg->ident = ident;
    msg->seq = seq;
    msg->originate = originate;
    msg->receive = 0;
    msg->transmit = 0;
}

void nmsg_fill_timestamp_reply(nmsg_timestamp_t *msg, uint16_t ident, uint16_t seq,
                               uint32_t originate, uint32_t receive, uint32_t transmit)
{
    if (msg == NULL)
        return;
    msg->type = 14;
    msg->ident = ident;
    msg->seq = seq;
    msg->originate = originate;
    msg->receive = receive;
    msg->transmit = transmit;
}

size_t nmsg_timestamp_serialize(uint8_t *out, size_t cap, const nmsg_timestamp_t *msg)
{
    if (out == NULL || msg == NULL || cap < 20)
        return 0;
    out[0] = msg->type;
    out[1] = 0;
    out[2] = 0;
    out[3] = 0;
    wire16(out + 4, msg->ident);
    wire16(out + 6, msg->seq);
    wire32(out + 8, msg->originate);
    wire32(out + 12, msg->receive);
    wire32(out + 16, msg->transmit);
    wire16(out + 2, wire_checksum(out, 20));
    return 20;
}
