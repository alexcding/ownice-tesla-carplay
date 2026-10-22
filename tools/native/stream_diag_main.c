#include "stream_diag.h"
/* Resolve repeated calls through our local PC-relative bridge, never a GOT. */
extern int remove(const char *) __attribute__((visibility("hidden")));
extern void *zq_diag_session(void);
extern int AirPlayReceiverSessionForceKeyFrame(void *, void *, void *);

void zq_diag_focus(unsigned hu_focus) {
    /* Reset checkpoints BEFORE the request. All original focus changes have
     * already run. Only lengths, NAL header bytes and scalar state are recorded. */
    remove(ZQ_DIAG_PHONE);
    remove(ZQ_DIAG_SENDER);
    remove(ZQ_DIAG_OUTPUT);
    void *session = zq_diag_session();
    int result = session ? AirPlayReceiverSessionForceKeyFrame(session, 0, 0) : -1;
    zq_diag_write(ZQ_DIAG_REQUEST, hu_focus, session != 0, (unsigned)result);
}

void zq_diag_phone(const unsigned char *data, unsigned len) {
    if (access(ZQ_DIAG_PHONE, 0) != 0)
        zq_diag_write(ZQ_DIAG_PHONE, len, zq_diag_nal(data, len), 0);
}
