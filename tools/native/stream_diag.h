/* Temporary receiver diagnostics: three hex integers, never frame payloads. */
extern void *fopen(const char *, const char *);
extern int fclose(void *);
extern unsigned fwrite(const void *, unsigned, unsigned, void *);
extern int access(const char *, int);
#define ZQ_DIAG_WEB "/usr/z-loader/zjinnova/web/"
#define ZQ_DIAG_PHONE ZQ_DIAG_WEB "zq-video-phone.txt"
#define ZQ_DIAG_SENDER ZQ_DIAG_WEB "zq-video-sender.txt"
#define ZQ_DIAG_OUTPUT ZQ_DIAG_WEB "zq-video-output.txt"
#define ZQ_DIAG_REQUEST ZQ_DIAG_WEB "zq-video-request.txt"

static void zq_diag_write(const char *path, unsigned a, unsigned b, unsigned c) {
    void *f = fopen(path, "w");
    if (!f) return;
    unsigned values[3] = {a, b, c};
    char line[27];
    for (unsigned v = 0; v < 3; ++v) {
        for (unsigned i = 0; i < 8; ++i) {
            unsigned n = (values[v] >> (28 - 4 * i)) & 15;
            line[v * 9 + i] = (char)(n < 10 ? '0' + n : 'a' + n - 10);
        }
        line[v * 9 + 8] = v == 2 ? '\n' : ' ';
    }
    fwrite(line, 1, sizeof(line), f);
    fclose(f);
}

static unsigned zq_diag_nal(const unsigned char *data, unsigned len) {
    if (!data || len < 4 || data[0] || data[1]) return 0xffffffffu;
    if (data[2] == 1) return data[3];
    if (!data[2] && data[3] == 1 && len >= 5) return data[4];
    return 0xffffffffu;
}
