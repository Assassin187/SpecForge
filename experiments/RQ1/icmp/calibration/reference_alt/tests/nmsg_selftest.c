#include <stdio.h>
#include <string.h>

#include "nmsg.h"

static int failures;

static void check(int condition, const char *name)
{
    if (condition) {
        printf("PASS %s\n", name);
    } else {
        printf("FAIL %s\n", name);
        failures++;
    }
}

int main(void)
{
    uint8_t buffer[128];
    uint8_t request[64];
    size_t length = 0;
    size_t request_len = 0;
    static const uint8_t data[5] = {0xDE, 0xAD, 0xBE, 0xEF, 0x01};
    static const uint8_t quotation[28] = {
        0x45, 0x00, 0x00, 0x3C, 0x12, 0x34, 0x40, 0x00,
        0x40, 0x06, 0x00, 0x00, 0xC0, 0xA8, 0x00, 0x01,
        0xC0, 0xA8, 0x00, 0x02, 0x51, 0x55, 0x4F, 0x54,
        0x45, 0x44, 0x36, 0x34
    };
    nmsg_unreach_t unreach;
    nmsg_timestamp_t timestamp;

    check(nmsg_echo_request_write(buffer, sizeof buffer, 0x1A2B, 0x3C4D,
                                  data, sizeof data, &length) == 0
          && length == 13 && buffer[0] == 8, "echo_request");
    check(nmsg_echo_request_write(buffer, 10, 0x1A2B, 0x3C4D,
                                  data, sizeof data, &length) != 0,
          "echo_request_capacity");

    check(nmsg_echo_request_write(request, sizeof request, 0x1A2B, 0x3C4D,
                                  data, sizeof data, &request_len) == 0,
          "echo_request_for_reply");
    check(nmsg_echo_reply_from(buffer, sizeof buffer, request, request_len, &length) == 0
          && length == request_len && buffer[0] == 0
          && memcmp(buffer + 4, request + 4, request_len - 4) == 0,
          "echo_reply_from_request");

    unreach.code = 3;
    unreach.quote = quotation;
    unreach.quote_len = sizeof quotation;
    check(nmsg_unreach_write(buffer, sizeof buffer, &unreach) == 36 && buffer[0] == 3
          && buffer[1] == 3, "destination_unreachable");
    check(nmsg_quench_write(buffer, sizeof buffer, quotation, sizeof quotation) == 36
          && buffer[0] == 4, "source_quench");
    check(nmsg_time_exceeded_write(buffer, sizeof buffer, quotation, sizeof quotation, 1) == 36
          && buffer[0] == 11 && buffer[1] == 1, "time_exceeded");
    check(nmsg_param_problem_write(buffer, sizeof buffer, 21, quotation,
                                   sizeof quotation) == 36
          && buffer[0] == 12 && buffer[4] == 21, "parameter_problem");
    check(nmsg_redirect_write(buffer, sizeof buffer, quotation, sizeof quotation,
                              0xC0A80063u, 1) == 36
          && buffer[0] == 5 && buffer[4] == 0xC0 && buffer[7] == 0x63, "redirect");

    nmsg_fill_timestamp_request(&timestamp, 7, 9, 0x02FAF080u);
    check(nmsg_timestamp_serialize(buffer, sizeof buffer, &timestamp) == 20
          && buffer[0] == 13 && buffer[12] == 0 && buffer[16] == 0, "timestamp_request");
    nmsg_fill_timestamp_reply(&timestamp, 7, 9, 0x02FAF080u, 0x02FAF3E4u, 0x82FAF7C8u);
    check(nmsg_timestamp_serialize(buffer, sizeof buffer, &timestamp) == 20
          && buffer[0] == 14 && buffer[16] == 0x82, "timestamp_reply");

    check(nmsg_info_request_write(buffer, sizeof buffer, 9, 7) == 8 && buffer[0] == 15,
          "information_request");
    check(nmsg_info_reply_write(buffer, sizeof buffer, 9, 7) == 8 && buffer[0] == 16,
          "information_reply");

    if (failures == 0) {
        printf("ALL DEVELOPMENT TESTS PASSED\n");
        return 0;
    }
    printf("%d DEVELOPMENT TEST(S) FAILED\n", failures);
    return 1;
}
