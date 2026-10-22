obj-m += tcp_hybrid.o
KDIR ?= /lib/modules/$(shell uname -r)/build

.PHONY: all clean test
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
test:
	$(CC) -std=c99 -Wall -Wextra -Werror test_hybrid.c -o /tmp/cs422-test-hybrid
	/tmp/cs422-test-hybrid
