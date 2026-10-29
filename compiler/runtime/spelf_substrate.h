#ifndef SPELF_SUBSTRATE_H
#define SPELF_SUBSTRATE_H

#include <stdint.h>

typedef struct {
    const char *name;
    uint32_t period_us;
    uint32_t deadline_us;
    int32_t priority;
    uint32_t stack_bytes;
    void (*entry)(void);
} spelf_task_t;

typedef struct {
    const char *name;
    uint32_t vector;
    int32_t level;
    uint32_t min_interarrival_us;
    void (*entry)(void);
} spelf_isr_t;

typedef struct {
    const char *name;
    uint32_t mode_mask;
    const uint32_t *period_us;
    const uint32_t *deadline_us;
} spelf_schedule_t;

extern const spelf_task_t spelf_tasks[];
extern const uint32_t spelf_task_count;

extern const spelf_isr_t spelf_isrs[] __attribute__((weak));
extern const uint32_t spelf_isr_count __attribute__((weak));
extern const spelf_schedule_t spelf_schedules[] __attribute__((weak));
extern const uint32_t spelf_schedule_count __attribute__((weak));
extern const uint32_t spelf_mode_count __attribute__((weak));
extern const uint8_t spelf_mode_schedule[] __attribute__((weak));

uint32_t spelf_protected_enter(int32_t ceiling);
void spelf_protected_exit(int32_t ceiling, uint32_t saved);

void spelf_cache_clean(const volatile void *p, uint32_t n);
void spelf_cache_invalidate(const volatile void *p, uint32_t n);
void spelf_mem_barrier(void);

void spelf_mode_change(uint32_t mode);

void spelf_overrun(uint32_t task_index);

#define SPELF_ISR_BASE 1000

#endif
