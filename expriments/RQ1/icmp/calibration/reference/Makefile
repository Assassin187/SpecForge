CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror -O2 -Iinclude -Isrc
SANFLAGS = -fsanitize=address,undefined -fno-omit-frame-pointer -g

SOURCES = src/icmp_checksum.c src/icmp_echo.c src/icmp_errors.c \
          src/icmp_timestamp.c src/icmp_info.c

all: icmp_selftest

icmp_selftest: $(SOURCES) tests/icmp_selftest.c
	$(CC) $(CFLAGS) -o $@ $(SOURCES) tests/icmp_selftest.c

sanitize: clean
	$(CC) $(CFLAGS) $(SANFLAGS) -o icmp_selftest $(SOURCES) tests/icmp_selftest.c

test: icmp_selftest
	./icmp_selftest

clean:
	rm -f icmp_selftest

.PHONY: all sanitize test clean
