#pragma once

#include "protocol/mqtt_packet.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*mqtt_on_packet_fn)(void* user, const mqtt_packet_t* pkt);

bool mqtt_decoder_feed(const uint8_t* buffer, size_t buffer_len, size_t* out_consumed, mqtt_on_packet_fn on_packet, void* user);

#ifdef __cplusplus
}
#endif
