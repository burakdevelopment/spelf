#include <stdint.h>
#include <stddef.h>

static volatile int32_t spelf_last_ceiling;

#if defined(__sparc__)
uint32_t spelf_protected_enter(int32_t ceiling) {
    uint32_t psr;
    __asm__ volatile("rd %%psr, %0" : "=r"(psr));
    __asm__ volatile("wr %0, 0, %%psr\n\tnop\n\tnop\n\tnop" : : "r"(psr | 0xF00u) : "memory");
    spelf_last_ceiling = ceiling;
    return psr & 0xF00u;
}
void spelf_protected_exit(int32_t ceiling, uint32_t saved) {
    (void)ceiling;
    uint32_t psr;
    __asm__ volatile("rd %%psr, %0" : "=r"(psr));
    psr = (psr & ~0xF00u) | (saved & 0xF00u);
    __asm__ volatile("wr %0, 0, %%psr\n\tnop\n\tnop\n\tnop" : : "r"(psr) : "memory");
}
#elif defined(__riscv)
uint32_t spelf_protected_enter(int32_t ceiling) {
    uint32_t old;
    __asm__ volatile("csrrci %0, mstatus, 8" : "=r"(old) : : "memory");
    spelf_last_ceiling = ceiling;
    return old & 8u;
}
void spelf_protected_exit(int32_t ceiling, uint32_t saved) {
    (void)ceiling;
    uint32_t m = saved & 8u;
    __asm__ volatile("csrs mstatus, %0" : : "r"(m) : "memory");
}
#elif defined(__arm__)
uint32_t spelf_protected_enter(int32_t ceiling) {
    uint32_t cpsr;
    __asm__ volatile("mrs %0, cpsr" : "=r"(cpsr));
    __asm__ volatile("cpsid i" : : : "memory");
    spelf_last_ceiling = ceiling;
    return cpsr & 0x80u;
}
void spelf_protected_exit(int32_t ceiling, uint32_t saved) {
    (void)ceiling;
    if ((saved & 0x80u) == 0u) {
        __asm__ volatile("cpsie i" : : : "memory");
    }
}
#else
uint32_t spelf_protected_enter(int32_t ceiling) { spelf_last_ceiling = ceiling; return 0u; }
void spelf_protected_exit(int32_t ceiling, uint32_t saved) {
    (void)ceiling; (void)saved; }
#endif

#if defined(__sparc__)
void spelf_mem_barrier(void) { __asm__ volatile("stbar" : : : "memory"); }
void spelf_cache_clean(const volatile void *p, uint32_t n) { (void)p; (void)n; spelf_mem_barrier(); }
void spelf_cache_invalidate(const volatile void *p, uint32_t n) {
    (void)p; (void)n;
    __asm__ volatile("sta %%g0, [%%g0] 0x11" : : : "memory");
}
#elif defined(__arm__)
#define SPELF_DLINE 32u
void spelf_mem_barrier(void) { __asm__ volatile("dsb" : : : "memory"); }
void spelf_cache_clean(const volatile void *p, uint32_t n) {
    uintptr_t a = (uintptr_t)p & ~(uintptr_t)(SPELF_DLINE - 1u);
    const uintptr_t end = (uintptr_t)p + n;
    for (; a < end; a += SPELF_DLINE) {
        __asm__ volatile("mcr p15, 0, %0, c7, c10, 1" : : "r"(a) : "memory");
    }
    spelf_mem_barrier();
}
void spelf_cache_invalidate(const volatile void *p, uint32_t n) {
    uintptr_t a = (uintptr_t)p & ~(uintptr_t)(SPELF_DLINE - 1u);
    const uintptr_t end = (uintptr_t)p + n;
    for (; a < end; a += SPELF_DLINE) {
        __asm__ volatile("mcr p15, 0, %0, c7, c6, 1" : : "r"(a) : "memory");
    }
    spelf_mem_barrier();
}
#elif defined(__riscv)
void spelf_mem_barrier(void) { __asm__ volatile("fence rw, rw" : : : "memory"); }
#if defined(__riscv_zicbom)
#define SPELF_DLINE 64u
void spelf_cache_clean(const volatile void *p, uint32_t n) {
    uintptr_t a = (uintptr_t)p & ~(uintptr_t)(SPELF_DLINE - 1u);
    for (; a < (uintptr_t)p + n; a += SPELF_DLINE) { __asm__ volatile("cbo.clean (%0)" : : "r"(a) : "memory"); }
    spelf_mem_barrier();
}
void spelf_cache_invalidate(const volatile void *p, uint32_t n) {
    uintptr_t a = (uintptr_t)p & ~(uintptr_t)(SPELF_DLINE - 1u);
    for (; a < (uintptr_t)p + n; a += SPELF_DLINE) { __asm__ volatile("cbo.inval (%0)" : : "r"(a) : "memory"); }
    spelf_mem_barrier();
}
#else
void spelf_cache_clean(const volatile void *p, uint32_t n) { (void)p; (void)n; spelf_mem_barrier(); }
void spelf_cache_invalidate(const volatile void *p, uint32_t n) { (void)p; (void)n; spelf_mem_barrier(); }
#endif
#else
void spelf_mem_barrier(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
void spelf_cache_clean(const volatile void *p, uint32_t n) { (void)p; (void)n; }
void spelf_cache_invalidate(const volatile void *p, uint32_t n) { (void)p; (void)n; }
#endif

void *memcpy(void *restrict dst, const void *restrict src, size_t n) {
    volatile uint8_t *d = (volatile uint8_t *)dst;
    const volatile uint8_t *s = (const volatile uint8_t *)src;
    for (size_t i = 0; i < n; ++i) { d[i] = s[i]; }
    return dst;
}
void *memset(void *dst, int c, size_t n) {
    volatile uint8_t *d = (volatile uint8_t *)dst;
    for (size_t i = 0; i < n; ++i) { d[i] = (uint8_t)c; }
    return dst;
}
