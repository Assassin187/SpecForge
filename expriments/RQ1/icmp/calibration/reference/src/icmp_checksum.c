#include "icmp_internal.h"

uint16_t icmp_checksum(const void *data, size_t length)
{
    const uint8_t *bytes = (const uint8_t *)data;
    uint32_t sum = 0;

    while (length >= 2) {
        sum += (uint16_t)(((uint16_t)bytes[0] << 8) | bytes[1]);
        bytes += 2;
        length -= 2;
    }
    if (length == 1)
        sum += (uint16_t)((uint16_t)bytes[0] << 8);
    while ((sum >> 16) != 0)
        sum = (sum & 0xFFFFu) + (sum >> 16);
    return (uint16_t)(~sum & 0xFFFFu);
}
