#ifndef NMSG_WIRE_H
#define NMSG_WIRE_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

static inline void wire16(uint8_t *at, uint16_t value)
{
    at[0] = (uint8_t)(value >> 8);
    at[1] = (uint8_t)(value & 0xFFu);
}

static inline void wire32(uint8_t *at, uint32_t value)
{
    at[0] = (uint8_t)(value >> 24);
    at[1] = (uint8_t)((value >> 16) & 0xFFu);
    at[2] = (uint8_t)((value >> 8) & 0xFFu);
    at[3] = (uint8_t)(value & 0xFFu);
}

static inline uint16_t wire_checksum(const uint8_t *bytes, size_t length)
{
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

#endif
