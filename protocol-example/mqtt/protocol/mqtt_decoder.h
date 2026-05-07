#pragma once

#include "mqtt_packet.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*mqtt_on_packet_fn)(void* user, const mqtt_packet_t* pkt);

// Parse as many complete MQTT packets as possible from a stream buffer.
//
// - buffer/buffer_len: input stream buffer (read-only to this function)
// - out_consumed: number of bytes consumed from the front of buffer
// - on_packet: called for each decoded packet (packet is valid only during callback)
//
// Returns false only on fatal errors (e.g., malformed fixed header encoding).
bool mqtt_decoder_feed(const uint8_t* buffer,
                       size_t buffer_len,
                       size_t* out_consumed,
                       mqtt_on_packet_fn on_packet,
                       void* user);

#ifdef __cplusplus
}
#endif
