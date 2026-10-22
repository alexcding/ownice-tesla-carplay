#include "stream_diag.h"

/* Reports must never consume a reconnect's first-video checkpoint, even if
 * the checkpoint is reset between the real-frame and report callbacks. */
static int zq_is_report(const unsigned char *data, unsigned len) {
    const char *uuid = "ownice-zq-diag01";
    if (!data || len < 25 || data[0] || data[1] || data[2] || data[3] != 1 ||
        data[4] != 0x4e || data[5] != 1 || data[6] != 5 ||
        9u + data[7] != len || data[len - 1] != 0x80) return 0;
    for (unsigned i = 0; i < 16; ++i) if (data[8 + i] != (unsigned char)uuid[i]) return 0;
    return 1;
}

void zq_diag_sender(const unsigned char *data, unsigned len,
                    const void *metadata, const unsigned char *context) {
    (void)metadata;
    if (zq_is_report(data, len)) return;
    if (access(ZQ_DIAG_SENDER, 0) == 0) return;
    unsigned state = 0xffffffffu;
    if (context) {
        const unsigned *words = (const unsigned *)context;
        state = (words[0x44 / 4] << 16) | (words[0x6c / 4] & 0xffff);
    }
    zq_diag_write(ZQ_DIAG_SENDER, len, zq_diag_nal(data, len), state);
}

void zq_diag_output(const unsigned char *data, unsigned len) {
    if (zq_is_report(data, len)) return;
    if (access(ZQ_DIAG_OUTPUT, 0) != 0)
        zq_diag_write(ZQ_DIAG_OUTPUT, len, zq_diag_nal(data, len), 0);
}
