#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    COAP_MAX_TOKEN_LEN = 8,
};

typedef enum coap_type {
    COAP_TYPE_CON = 0,
    COAP_TYPE_NON = 1,
    COAP_TYPE_ACK = 2,
    COAP_TYPE_RST = 3,
} coap_type_t;

typedef enum coap_method {
    COAP_METHOD_GET = 1,
    COAP_METHOD_POST = 2,
    COAP_METHOD_PUT = 3,
    COAP_METHOD_DELETE = 4,
} coap_method_t;

typedef enum coap_option_number {
    COAP_OPT_URI_PATH = 11,
    COAP_OPT_CONTENT_FORMAT = 12,
    COAP_OPT_URI_QUERY = 15,
    COAP_OPT_ACCEPT = 17,
} coap_option_number_t;

typedef enum coap_content_format {
    COAP_FORMAT_TEXT_PLAIN = 0,
    COAP_FORMAT_LINK_FORMAT = 40,
    COAP_FORMAT_OCTET_STREAM = 42,
    COAP_FORMAT_APPLICATION_JSON = 50,
} coap_content_format_t;

typedef struct coap_option {
    uint16_t number;
    uint16_t length;
    uint8_t* value;
} coap_option_t;

typedef struct coap_message {
    uint8_t version;
    coap_type_t type;
    uint8_t code;
    uint16_t message_id;
    uint8_t token_len;
    uint8_t token[COAP_MAX_TOKEN_LEN];
    coap_option_t* options;
    size_t option_count;
    uint8_t* payload;
    size_t payload_len;
} coap_message_t;

typedef struct coap_bytes {
    uint8_t* data;
    size_t len;
} coap_bytes_t;

void coap_message_init(coap_message_t* msg);
void coap_message_reset(coap_message_t* msg);
void coap_message_free(coap_message_t* msg);

bool coap_message_set_token(coap_message_t* msg, const uint8_t* token, size_t token_len);
bool coap_message_set_payload(coap_message_t* msg, const uint8_t* data, size_t len);
bool coap_message_set_payload_string(coap_message_t* msg, const char* s);
bool coap_message_add_option(coap_message_t* msg, uint16_t number, const uint8_t* value, size_t len);
bool coap_message_add_option_string(coap_message_t* msg, uint16_t number, const char* s);
bool coap_message_add_option_uint(coap_message_t* msg, uint16_t number, uint32_t value);

bool coap_message_get_uint_option(const coap_message_t* msg, uint16_t number, uint32_t* out);
char* coap_message_join_options(const coap_message_t* msg, uint16_t number, char sep);

uint8_t coap_make_code(uint8_t code_class, uint8_t detail);
uint8_t coap_code_class(uint8_t code);
uint8_t coap_code_detail(uint8_t code);
bool coap_code_is_method(uint8_t code);

void coap_bytes_free(coap_bytes_t* bytes);

#ifdef __cplusplus
}
#endif
