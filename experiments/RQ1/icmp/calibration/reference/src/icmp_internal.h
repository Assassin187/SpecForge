#ifndef ICMP_INTERNAL_H
#define ICMP_INTERNAL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "icmp_messages.h"

static inline void put16(uint8_t *at, uint16_t value)
{
    at[0] = (uint8_t)(value >> 8);
    at[1] = (uint8_t)(value & 0xFFu);
}

static inline void put32(uint8_t *at, uint32_t value)
{
    at[0] = (uint8_t)(value >> 24);
    at[1] = (uint8_t)((value >> 16) & 0xFFu);
    at[2] = (uint8_t)((value >> 8) & 0xFFu);
    at[3] = (uint8_t)(value & 0xFFu);
}

#endif
