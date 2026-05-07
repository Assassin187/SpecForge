#include "coap_codec.h"

#include <stdlib.h>
#include <string.h>

static size_t option_ext_len(uint16_t value) {
    if (value < 13) {
        return 0;
    }
    if (value < 269) {
        return 1;
    }
    return 2;
}

static bool encode_ext(uint16_t value, uint8_t nibble, uint8_t* out, size_t* out_len) {
    if (!out_len) {
        return false;
    }
    *out_len = 0;
    if (nibble < 13) {
        return true;
    }
    if (nibble == 13) {
        out[0] = (uint8_t)(value - 13);
        *out_len = 1;
        return true;
    }
    if (nibble == 14) {
        const uint16_t v = (uint16_t)(value - 269);
        out[0] = (uint8_t)((v >> 8) & 0xffu);
        out[1] = (uint8_t)(v & 0xffu);
        *out_len = 2;
        return true;
    }
    return false;
}

static uint8_t encode_nibble(uint16_t value) {
    if (value < 13) {
        return (uint8_t)value;
    }
    if (value < 269) {
        return 13;
    }
    return 14;
}

static bool decode_ext(const uint8_t* data,
                       size_t len,
                       size_t* off,
                       uint8_t nibble,
                       uint16_t* out_value) {
    if (!off || !out_value) {
        return false;
    }
    if (nibble < 13) {
        *out_value = nibble;
        return true;
    }
    if (nibble == 13) {
        if (*off >= len) {
            return false;
        }
        *out_value = (uint16_t)(13 + data[(*off)++]);
        return true;
    }
    if (nibble == 14) {
        if (*off + 1 >= len) {
            return false;
        }
        *out_value = (uint16_t)(269 + ((uint16_t)data[*off] << 8) + data[*off + 1]);
        *off += 2;
        return true;
    }
    return false;
}

coap_decode_status_t coap_decode_message(const uint8_t* data, size_t len, coap_message_t* out) {
    if (!data || len < 4 || !out) {
        return COAP_DECODE_TRUNCATED;
    }

    coap_message_init(out);

    out->version = (uint8_t)((data[0] >> 6) & 0x3u);
    out->type = (coap_type_t)((data[0] >> 4) & 0x3u);
    out->token_len = (uint8_t)(data[0] & 0x0fu);
    out->code = data[1];
    out->message_id = (uint16_t)(((uint16_t)data[2] << 8) | data[3]);

    if (out->version != 1 || out->token_len > COAP_MAX_TOKEN_LEN) {
        coap_message_free(out);
        return COAP_DECODE_INVALID;
    }
    if (len < 4u + out->token_len) {
        coap_message_free(out);
        return COAP_DECODE_TRUNCATED;
    }

    if (out->token_len > 0) {
        memcpy(out->token, data + 4, out->token_len);
    }

    size_t off = 4u + out->token_len;
    uint16_t current_number = 0;

    while (off < len) {
        if (data[off] == 0xffu) {
            off += 1;
            break;
        }

        const uint8_t byte = data[off++];
        const uint8_t delta_nibble = (uint8_t)((byte >> 4) & 0x0fu);
        const uint8_t len_nibble = (uint8_t)(byte & 0x0fu);

        if (delta_nibble == 15 || len_nibble == 15) {
            coap_message_free(out);
            return COAP_DECODE_INVALID;
        }

        uint16_t delta = 0;
        uint16_t opt_len = 0;
        if (!decode_ext(data, len, &off, delta_nibble, &delta) ||
            !decode_ext(data, len, &off, len_nibble, &opt_len)) {
            coap_message_free(out);
            return COAP_DECODE_TRUNCATED;
        }

        current_number = (uint16_t)(current_number + delta);
        if (off + opt_len > len) {
            coap_message_free(out);
            return COAP_DECODE_TRUNCATED;
        }

        if (!coap_message_add_option(out, current_number, data + off, opt_len)) {
            coap_message_free(out);
            return COAP_DECODE_INVALID;
        }
        off += opt_len;
    }

    if (off < len) {
        if (!coap_message_set_payload(out, data + off, len - off)) {
            coap_message_free(out);
            return COAP_DECODE_INVALID;
        }
    } else if (off > len) {
        coap_message_free(out);
        return COAP_DECODE_TRUNCATED;
    }

    return COAP_DECODE_OK;
}

bool coap_encode_message(const coap_message_t* msg, coap_bytes_t* out) {
    if (!msg || !out || msg->version != 1 || msg->token_len > COAP_MAX_TOKEN_LEN) {
        return false;
    }

    size_t total = 4 + msg->token_len;
    uint16_t last_number = 0;
    for (size_t i = 0; i < msg->option_count; ++i) {
        const coap_option_t* opt = &msg->options[i];
        if (opt->number < last_number) {
            return false;
        }
        const uint16_t delta = (uint16_t)(opt->number - last_number);
        total += 1 + option_ext_len(delta) + option_ext_len(opt->length) + opt->length;
        last_number = opt->number;
    }
    if (msg->payload_len > 0) {
        total += 1 + msg->payload_len;
    }

    uint8_t* data = (uint8_t*)malloc(total);
    if (!data) {
        return false;
    }

    size_t off = 0;
    data[off++] = (uint8_t)(((msg->version & 0x3u) << 6) | (((uint8_t)msg->type & 0x3u) << 4) |
                            (msg->token_len & 0x0fu));
    data[off++] = msg->code;
    data[off++] = (uint8_t)((msg->message_id >> 8) & 0xffu);
    data[off++] = (uint8_t)(msg->message_id & 0xffu);

    if (msg->token_len > 0) {
        memcpy(data + off, msg->token, msg->token_len);
        off += msg->token_len;
    }

    last_number = 0;
    for (size_t i = 0; i < msg->option_count; ++i) {
        const coap_option_t* opt = &msg->options[i];
        const uint16_t delta = (uint16_t)(opt->number - last_number);
        const uint8_t delta_nibble = encode_nibble(delta);
        const uint8_t len_nibble = encode_nibble(opt->length);
        data[off++] = (uint8_t)((delta_nibble << 4) | len_nibble);

        uint8_t ext[2];
        size_t ext_len = 0;
        if (!encode_ext(delta, delta_nibble, ext, &ext_len)) {
            free(data);
            return false;
        }
        if (ext_len > 0) {
            memcpy(data + off, ext, ext_len);
            off += ext_len;
        }

        if (!encode_ext(opt->length, len_nibble, ext, &ext_len)) {
            free(data);
            return false;
        }
        if (ext_len > 0) {
            memcpy(data + off, ext, ext_len);
            off += ext_len;
        }

        if (opt->length > 0) {
            memcpy(data + off, opt->value, opt->length);
            off += opt->length;
        }
        last_number = opt->number;
    }

    if (msg->payload_len > 0) {
        data[off++] = 0xffu;
        memcpy(data + off, msg->payload, msg->payload_len);
        off += msg->payload_len;
    }

    out->data = data;
    out->len = off;
    return true;
}
