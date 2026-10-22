#include "stream_diag.h"

void zq_diag_sender(const unsigned char *data, unsigned len,
                    const void *metadata, const unsigned char *context) {
    (void)metadata;
    if (access(ZQ_DIAG_SENDER, 0) == 0) return;
    unsigned state = 0xffffffffu;
    if (context) {
        const unsigned *words = (const unsigned *)context;
        state = (words[0x44 / 4] << 16) | (words[0x6c / 4] & 0xffff);
    }
    zq_diag_write(ZQ_DIAG_SENDER, len, zq_diag_nal(data, len), state);
}

void zq_diag_output(const unsigned char *data, unsigned len) {
    if (access(ZQ_DIAG_OUTPUT, 0) != 0)
        zq_diag_write(ZQ_DIAG_OUTPUT, len, zq_diag_nal(data, len), 0);
}
