CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror -O2 -Iinclude -Isrc
SANFLAGS = -fsanitize=address,undefined -fno-omit-frame-pointer -g

SOURCES = src/nmsg_echo.c src/nmsg_errors.c src/nmsg_timestamp.c src/nmsg_info.c

all: nmsg_selftest

nmsg_selftest: $(SOURCES) tests/nmsg_selftest.c
	$(CC) $(CFLAGS) -o $@ $(SOURCES) tests/nmsg_selftest.c

sanitize: clean
	$(CC) $(CFLAGS) $(SANFLAGS) -o nmsg_selftest $(SOURCES) tests/nmsg_selftest.c

test: nmsg_selftest
	./nmsg_selftest

clean:
	rm -f nmsg_selftest

.PHONY: all sanitize test clean
