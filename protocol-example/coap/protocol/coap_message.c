#include "coap_message.h"

#include <stdlib.h>
#include <string.h>

static void* xrealloc(void* p, size_t n) {
    return realloc(p, n);
}

void coap_message_init(coap_message_t* msg) {
    if (!msg) {
        return;
    }
    memset(msg, 0, sizeof(*msg));
    msg->version = 1;
}

void coap_message_reset(coap_message_t* msg) {
    if (!msg) {
        return;
    }
    for (size_t i = 0; i < msg->option_count; ++i) {
        free(msg->options[i].value);
    }
    free(msg->options);
    free(msg->payload);
    coap_message_init(msg);
}

void coap_message_free(coap_message_t* msg) {
    coap_message_reset(msg);
}

bool coap_message_set_token(coap_message_t* msg, const uint8_t* token, size_t token_len) {
    if (!msg || token_len > COAP_MAX_TOKEN_LEN) {
        return false;
    }
    msg->token_len = (uint8_t)token_len;
    if (token_len > 0 && token) {
        memcpy(msg->token, token, token_len);
    }
    return true;
}

bool coap_message_set_payload(coap_message_t* msg, const uint8_t* data, size_t len) {
    if (!msg) {
        return false;
    }
    free(msg->payload);
    msg->payload = NULL;
    msg->payload_len = 0;
    if (!data || len == 0) {
        return true;
    }
    msg->payload = (uint8_t*)malloc(len);
    if (!msg->payload) {
        return false;
    }
    memcpy(msg->payload, data, len);
    msg->payload_len = len;
    return true;
}

bool coap_message_set_payload_string(coap_message_t* msg, const char* s) {
    if (!s) {
        return coap_message_set_payload(msg, NULL, 0);
    }
    return coap_message_set_payload(msg, (const uint8_t*)s, strlen(s));
}

bool coap_message_add_option(coap_message_t* msg, uint16_t number, const uint8_t* value, size_t len) {
    if (!msg || len > UINT16_MAX) {
        return false;
    }

    coap_option_t* opts =
        (coap_option_t*)xrealloc(msg->options, (msg->option_count + 1) * sizeof(*msg->options));
    if (!opts) {
        return false;
    }
    msg->options = opts;

    coap_option_t* opt = &msg->options[msg->option_count];
    memset(opt, 0, sizeof(*opt));
    opt->number = number;
    opt->length = (uint16_t)len;
    if (len > 0) {
        opt->value = (uint8_t*)malloc(len);
        if (!opt->value) {
            return false;
        }
        memcpy(opt->value, value, len);
    }

    msg->option_count += 1;
    return true;
}

bool coap_message_add_option_string(coap_message_t* msg, uint16_t number, const char* s) {
    if (!s) {
        return coap_message_add_option(msg, number, NULL, 0);
    }
    return coap_message_add_option(msg, number, (const uint8_t*)s, strlen(s));
}

bool coap_message_add_option_uint(coap_message_t* msg, uint16_t number, uint32_t value) {
    uint8_t buf[4];
    size_t len = 0;

    if (value == 0) {
        return coap_message_add_option(msg, number, NULL, 0);
    }

    for (int i = 3; i >= 0; --i) {
        buf[i] = (uint8_t)(value & 0xffu);
        value >>= 8;
    }
    while (len < sizeof(buf) && buf[len] == 0) {
        ++len;
    }
    return coap_message_add_option(msg, number, buf + len, sizeof(buf) - len);
}

bool coap_message_get_uint_option(const coap_message_t* msg, uint16_t number, uint32_t* out) {
    if (out) {
        *out = 0;
    }
    if (!msg) {
        return false;
    }
    for (size_t i = 0; i < msg->option_count; ++i) {
        const coap_option_t* opt = &msg->options[i];
        if (opt->number != number) {
            continue;
        }
        if (opt->length > 4) {
            return false;
        }
        uint32_t value = 0;
        for (size_t j = 0; j < opt->length; ++j) {
            value = (value << 8) | opt->value[j];
        }
        if (out) {
            *out = value;
        }
        return true;
    }
    return false;
}

char* coap_message_join_options(const coap_message_t* msg, uint16_t number, char sep) {
    if (!msg) {
        return NULL;
    }

    size_t total = 0;
    size_t count = 0;
    for (size_t i = 0; i < msg->option_count; ++i) {
        const coap_option_t* opt = &msg->options[i];
        if (opt->number != number) {
            continue;
        }
        total += opt->length;
        if (count > 0) {
            total += 1;
        }
        count += 1;
    }

    if (count == 0) {
        char* empty = (char*)calloc(1, 1);
        return empty;
    }

    char* out = (char*)malloc(total + 1);
    if (!out) {
        return NULL;
    }

    size_t off = 0;
    size_t seen = 0;
    for (size_t i = 0; i < msg->option_count; ++i) {
        const coap_option_t* opt = &msg->options[i];
        if (opt->number != number) {
            continue;
        }
        if (seen > 0) {
            out[off++] = sep;
        }
        if (opt->length > 0) {
            memcpy(out + off, opt->value, opt->length);
            off += opt->length;
        }
        seen += 1;
    }
    out[off] = '\0';
    return out;
}

uint8_t coap_make_code(uint8_t code_class, uint8_t detail) {
    return (uint8_t)(((code_class & 0x7u) << 5) | (detail & 0x1fu));
}

uint8_t coap_code_class(uint8_t code) {
    return (uint8_t)((code >> 5) & 0x7u);
}

uint8_t coap_code_detail(uint8_t code) {
    return (uint8_t)(code & 0x1fu);
}

bool coap_code_is_method(uint8_t code) {
    return coap_code_class(code) == 0 && code >= 1 && code <= 4;
}

void coap_bytes_free(coap_bytes_t* bytes) {
    if (!bytes) {
        return;
    }
    free(bytes->data);
    bytes->data = NULL;
    bytes->len = 0;
}
