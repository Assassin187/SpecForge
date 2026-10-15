#include <stdio.h>
#include <string.h>

#include "icmp_messages.h"

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

static int valid_checksum(const uint8_t *message, size_t length)
{
    return icmp_checksum(message, length) == 0;
}

int main(void)
{
    uint8_t buffer[128];
    size_t length;
    static const uint8_t odd_data[5] = {0xDE, 0xAD, 0xBE, 0xEF, 0x01};
    static const uint8_t quotation[28] = {
        0x45, 0x00, 0x00, 0x3C, 0x12, 0x34, 0x40, 0x00,
        0x40, 0x06, 0x00, 0x00, 0xC0, 0xA8, 0x00, 0x01,
        0xC0, 0xA8, 0x00, 0x02, 0x51, 0x55, 0x4F, 0x54,
        0x45, 0x44, 0x36, 0x34
    };

    length = icmp_build_echo_request(buffer, sizeof buffer, 0x1A2B, 0x3C4D, odd_data, 5);
    check(length == 13 && buffer[0] == 8 && buffer[1] == 0, "echo_request_fields");
    check(length == 13 && valid_checksum(buffer, length), "echo_request_checksum");
    check(buffer[4] == 0x1A && buffer[5] == 0x2B, "echo_request_identifier");

    length = icmp_build_echo_reply(buffer, sizeof buffer, 0x1A2B, 0x3C4D, odd_data, 5);
    check(length == 13 && buffer[0] == 0 && valid_checksum(buffer, length), "echo_reply");

    length = icmp_build_echo_request(buffer, sizeof buffer, 1, 1, NULL, 0);
    check(length == 8 && valid_checksum(buffer, length), "echo_empty_data");

    length = icmp_build_echo_request(buffer, 10, 0x1A2B, 0x3C4D, odd_data, 5);
    check(length == 0, "echo_capacity_rejected");

    length = icmp_build_destination_unreachable(buffer, sizeof buffer, 3,
                                                quotation, sizeof quotation);
    check(length == 36 && buffer[0] == 3 && buffer[1] == 3, "unreachable_fields");
    check(valid_checksum(buffer, length), "unreachable_checksum");
    check(buffer[4] == 0 && buffer[5] == 0 && buffer[6] == 0 && buffer[7] == 0,
          "unreachable_unused_zero");
    check(memcmp(buffer + 8, quotation, sizeof quotation) == 0, "unreachable_quotation");

    length = icmp_build_redirect(buffer, sizeof buffer, 1, 0xC0A80063u,
                                 quotation, sizeof quotation);
    check(length == 36 && buffer[0] == 5 && buffer[1] == 1, "redirect_fields");
    check(buffer[4] == 0xC0 && buffer[5] == 0xA8 && buffer[6] == 0 && buffer[7] == 0x63,
          "redirect_gateway");

    length = icmp_build_time_exceeded(buffer, sizeof buffer, 1, quotation, sizeof quotation);
    check(length == 36 && buffer[0] == 11 && buffer[1] == 1, "time_exceeded");

    length = icmp_build_parameter_problem(buffer, sizeof buffer, 21, quotation, sizeof quotation);
    check(length == 36 && buffer[0] == 12 && buffer[4] == 21, "parameter_problem_pointer");
    check(buffer[5] == 0 && buffer[6] == 0 && buffer[7] == 0, "parameter_problem_unused_zero");

    length = icmp_build_source_quench(buffer, sizeof buffer, quotation, sizeof quotation);
    check(length == 36 && buffer[0] == 4 && buffer[1] == 0, "source_quench");

    length = icmp_build_timestamp_request(buffer, sizeof buffer, 7, 9, 0x02FAF080u);
    check(length == 20 && buffer[0] == 13, "timestamp_request");
    check(buffer[12] == 0 && buffer[13] == 0 && buffer[16] == 0 && buffer[17] == 0,
          "timestamp_request_zeros");

    length = icmp_build_timestamp_reply(buffer, sizeof buffer, 7, 9,
                                        0x02FAF080u, 0x02FAF3E4u, 0x82FAF7C8u);
    check(length == 20 && buffer[0] == 14 && buffer[16] == 0x82, "timestamp_reply");
    check(valid_checksum(buffer, length), "timestamp_reply_checksum");

    length = icmp_build_information_request(buffer, sizeof buffer, 7, 9);
    check(length == 8 && buffer[0] == 15, "information_request");

    length = icmp_build_information_reply(buffer, sizeof buffer, 7, 9);
    check(length == 8 && buffer[0] == 16 && valid_checksum(buffer, length),
          "information_reply");

    if (failures == 0) {
        printf("ALL DEVELOPMENT TESTS PASSED\n");
        return 0;
    }
    printf("%d DEVELOPMENT TEST(S) FAILED\n", failures);
    return 1;
}
