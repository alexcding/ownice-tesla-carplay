/* Codec advertisement only. Existing authentication/session checks run first. */
extern void *CFDictionaryCreateMutable(void *, long, const void *, const void *);
extern void CFDictionarySetValue(void *, const void *, const void *);
extern void *CFDictionaryGetValue(const void *, const void *);
extern void *CFArrayCreateMutable(void *, long, const void *);
extern void CFArrayAppendValue(void *, const void *);
extern void *CFStringCreateWithCString(void *, const char *, unsigned);
extern void CFRelease(const void *);
extern const void *zq_dict_keys(void), *zq_dict_values(void), *zq_array_values(void);
extern void *fopen(const char *, const char *);
extern int fclose(void *);

static int hevc_enabled(void) {
    void *flag = fopen("/data/local/tmp/zq_hevc", "r");
    if (!flag) return 0;
    fclose(flag);
    return 1;
}

void *zq_info(void *info) {
    if (!info || !hevc_enabled()) return info;
    void *key = CFStringCreateWithCString(0, "hevcInfo", 0x08000100);
    if (!key) return info;
    void *hevc = CFDictionaryCreateMutable(0, 0, zq_dict_keys(), zq_dict_values());
    if (hevc) { CFDictionarySetValue(info, key, hevc); CFRelease(hevc); }
    CFRelease(key);
    return info;
}
void zq_setup(void *response) {
    if (!response || !hevc_enabled()) return;
    void *key = CFStringCreateWithCString(0, "enabledFeatures", 0x08000100);
    if (!key) return;
    /* The hash-pinned stock build has no enabledFeatures. Do not overwrite a
     * field unexpectedly supplied by a platform delegate. */
    if (!CFDictionaryGetValue(response, key)) {
        void *features = CFArrayCreateMutable(0, 0, zq_array_values());
        void *hevc = CFStringCreateWithCString(0, "hevc", 0x08000100);
        if (features && hevc) {
            CFArrayAppendValue(features, hevc);
            CFDictionarySetValue(response, key, features);
        }
        if (hevc) CFRelease(hevc);
        if (features) CFRelease(features);
    }
    CFRelease(key);
}
