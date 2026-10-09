#ifndef ICMP_MESSAGES_H
#define ICMP_MESSAGES_H

#include <stddef.h>
#include <stdint.h>

/*
 * RFC 792 (September 1981) ICMPv4 message construction.
 *
 * Every constructor serializes one complete ICMP message, starting at the
 * Type field, into the caller-supplied output buffer `out` whose capacity in
 * bytes is `out_cap`. On success the exact serialized length is returned;
 * the Checksum field is computed over the whole message with the field
 * itself cleared, padding an odd-length message with one zero octet for the
 * calculation only. If `out_cap` is too small (or an input pointer is
 * missing) the constructor returns 0 and writes nothing beyond `out_cap`.
 *
 * Multi-byte fields are serialized in network byte order. Fields that
 * RFC 792 marks unused are emitted as zero. Ownership of all buffers stays
 * with the caller; no constructor allocates memory.
 *
 * Timestamps are 32-bit milliseconds since midnight UT; a value with the
 * high bit set is a nonstandard-time value and is carried verbatim.
 * Acquiring the current time is the caller's responsibility.
 *
 * Error messages (Destination Unreachable, Source Quench, Redirect, Time
 * Exceeded, Parameter Problem) embed a caller-supplied quotation: the
 * original IPv4 header (including options when present) plus the first
 * 64 bits of its data.
 */

uint16_t icmp_checksum(const void *data, size_t length);

size_t icmp_build_echo_request(uint8_t *out, size_t out_cap,
                               uint16_t identifier, uint16_t sequence,
                               const void *data, size_t data_len);
size_t icmp_build_echo_reply(uint8_t *out, size_t out_cap,
                             uint16_t identifier, uint16_t sequence,
                             const void *data, size_t data_len);

size_t icmp_build_destination_unreachable(uint8_t *out, size_t out_cap,
                                          uint8_t code,
                                          const uint8_t *quotation, size_t quotation_len);
size_t icmp_build_source_quench(uint8_t *out, size_t out_cap,
                                const uint8_t *quotation, size_t quotation_len);
size_t icmp_build_redirect(uint8_t *out, size_t out_cap, uint8_t code,
                           uint32_t gateway_address,
                           const uint8_t *quotation, size_t quotation_len);
size_t icmp_build_time_exceeded(uint8_t *out, size_t out_cap, uint8_t code,
                                const uint8_t *quotation, size_t quotation_len);
size_t icmp_build_parameter_problem(uint8_t *out, size_t out_cap, uint8_t pointer,
                                    const uint8_t *quotation, size_t quotation_len);

size_t icmp_build_timestamp_request(uint8_t *out, size_t out_cap,
                                    uint16_t identifier, uint16_t sequence,
                                    uint32_t originate_timestamp);
size_t icmp_build_timestamp_reply(uint8_t *out, size_t out_cap,
                                  uint16_t identifier, uint16_t sequence,
                                  uint32_t originate_timestamp,
                                  uint32_t receive_timestamp,
                                  uint32_t transmit_timestamp);

size_t icmp_build_information_request(uint8_t *out, size_t out_cap,
                                      uint16_t identifier, uint16_t sequence);
size_t icmp_build_information_reply(uint8_t *out, size_t out_cap,
                                    uint16_t identifier, uint16_t sequence);

#endif
