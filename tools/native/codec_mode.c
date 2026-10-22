/* Called at the stock mode writer's shared exit, after its persistence result.
 * This setting only changes negotiation on the next phone connection. */
extern void *fopen(const char *, const char *);
extern int fclose(void *);
extern int unlink(const char *);
extern int access(const char *, int);
extern unsigned fwrite(const void *, unsigned, unsigned, void *);
extern int zq_write_file(const char *, const void *, unsigned);
extern int system(const char *);

int zq_mode_loaded(int mode) {
    if (mode != 0) return mode;
    /* A failed rollback or interrupted update may leave the mode file and flag
     * inconsistent. Never boot into local JPEG decoding while HEVC is offered. */
    void *flag = fopen("/data/local/tmp/zq_hevc", "r");
    if (!flag) return mode;
    fclose(flag);
    return 1;
}

static int failed(void) {
    /* The stock callers used to ignore errors. Their new continuations force
     * compressed passthrough on failure; keep reboot persistence safe too.
     * Call the stock file helper directly, avoiding recursion through this hook. */
    zq_write_file("/data/local/tmp/local_is_h264", "1", 1);
    void *log = fopen("/usr/z-loader/zjinnova/web/zq-codec.txt", "w");
    if (log) {
        static const char message[] = "Codec change failed; compressed passthrough retained. Retry the setting before reconnecting the phone.\n";
        fwrite(message, 1, sizeof(message) - 1, log);
        fclose(log);
    }
    system("sync");
    return -1;
}

int zq_mode_saved(int result, int mode) {
    const char *path = "/data/local/tmp/zq_hevc";
    if (result) return failed(); /* Do not change negotiation after a failed save. */
    if (mode == 2) {
        void *flag = fopen(path, "w");
        if (!flag || fclose(flag) != 0) return failed();
    } else {
        /* An already absent flag is success; report other removal failures. */
        if (unlink(path) != 0 && access(path, 0) == 0) return failed();
    }
    unlink("/usr/z-loader/zjinnova/web/zq-codec.txt");
    system("sync");
    return 0;
}
