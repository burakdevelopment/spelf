#if defined(__has_include)
#  if __has_include(<rtems.h>)
#    include <rtems.h>
#  else
#    error "RTEMS headers not found (rtems.h)"
#  endif
#else
#  include <rtems.h>
#endif
#include <stddef.h>
#include "spelf_substrate.h"

#ifndef SPELF_MAX_TASKS
#define SPELF_MAX_TASKS 64u
#endif
#ifndef SPELF_STACK_MARGIN
#define SPELF_STACK_MARGIN 1024u
#endif

#define SPELF_EV_WAKE RTEMS_EVENT_0
#define SPELF_FATAL(code) rtems_fatal(RTEMS_FATAL_SOURCE_APPLICATION, (rtems_fatal_code)(0x53504C00u | (code)))

static rtems_id spelf_tid[SPELF_MAX_TASKS];
static volatile uint32_t spelf_cur_sched;
static volatile uint32_t spelf_overrun_count[SPELF_MAX_TASKS];

static rtems_task_priority spelf_prio(int32_t p) {
    return (rtems_task_priority)(256 - p);
}

static int spelf_has_schedules(void) {
    return &spelf_schedule_count != NULL && spelf_schedule_count > 0u;
}

static uint32_t spelf_period_of(uint32_t i) {
    if (!spelf_has_schedules()) {
        return spelf_tasks[i].period_us;
    }
    return spelf_schedules[spelf_cur_sched].period_us[i];
}

static rtems_interval spelf_ticks(uint32_t us) {
    uint64_t t = (uint64_t)us * (uint64_t)rtems_clock_get_ticks_per_second();
    if (t == 0u || t % 1000000u != 0u) {
        SPELF_FATAL(0x01u);
    }
    return (rtems_interval)(t / 1000000u);
}

uint32_t spelf_protected_enter(int32_t ceiling) {
    if (ceiling >= SPELF_ISR_BASE) {
        rtems_interrupt_level level;
        rtems_interrupt_local_disable(level);
        return (uint32_t)level;
    }
    rtems_task_priority old = 0;
    if (rtems_task_set_priority(RTEMS_SELF, spelf_prio(ceiling), &old) != RTEMS_SUCCESSFUL) {
        SPELF_FATAL(0x02u);
    }
    return (uint32_t)old;
}

void spelf_protected_exit(int32_t ceiling, uint32_t saved) {
    if (ceiling >= SPELF_ISR_BASE) {
        rtems_interrupt_level level = (rtems_interrupt_level)saved;
        rtems_interrupt_local_enable(level);
        return;
    }
    rtems_task_priority ignored = 0;
    if (rtems_task_set_priority(RTEMS_SELF, (rtems_task_priority)saved, &ignored) != RTEMS_SUCCESSFUL) {
        SPELF_FATAL(0x03u);
    }
}

void spelf_mem_barrier(void) {
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}
void spelf_cache_clean(const volatile void *p, uint32_t n) {
    rtems_cache_flush_multiple_data_lines((const void *)p, (size_t)n);
}
void spelf_cache_invalidate(const volatile void *p, uint32_t n) {
    rtems_cache_invalidate_multiple_data_lines((const void *)p, (size_t)n);
}

__attribute__((weak)) void spelf_overrun(uint32_t task_index) {
    if (task_index < SPELF_MAX_TASKS) {
        spelf_overrun_count[task_index]++;
    }
}

static rtems_task spelf_task_body(rtems_task_argument arg) {
    const uint32_t i = (uint32_t)arg;
    rtems_id rm;
    if (rtems_rate_monotonic_create(rtems_build_name('S', 'P', 'R', (char)('A' + (i % 26u))), &rm)
            != RTEMS_SUCCESSFUL) {
        SPELF_FATAL(0x04u);
    }
    for (;;) {
        const uint32_t p_us = spelf_period_of(i);
        if (p_us == 0u) {
            (void)rtems_rate_monotonic_cancel(rm);
            rtems_event_set ev = 0;
            (void)rtems_event_receive(SPELF_EV_WAKE, RTEMS_WAIT | RTEMS_EVENT_ANY, RTEMS_NO_TIMEOUT, &ev);
            continue;
        }
        if (rtems_rate_monotonic_period(rm, spelf_ticks(p_us)) == RTEMS_TIMEOUT) {
            spelf_overrun(i);
        }
        spelf_tasks[i].entry();
    }
}

static void spelf_isr_tramp(void *arg) {
    spelf_isrs[(uintptr_t)arg].entry();
}

void spelf_mode_change(uint32_t mode) {
    if (!spelf_has_schedules() || &spelf_mode_count == NULL || mode >= spelf_mode_count) {
        SPELF_FATAL(0x05u);
    }
    const uint32_t s = spelf_mode_schedule[mode];
    rtems_interrupt_level level;
    rtems_interrupt_local_disable(level);
    const uint32_t old = spelf_cur_sched;
    spelf_cur_sched = s;
    rtems_interrupt_local_enable(level);
    if (old == s) {
        return;
    }
    for (uint32_t i = 0; i < spelf_task_count; i++) {
        if (spelf_schedules[old].period_us[i] == 0u && spelf_schedules[s].period_us[i] != 0u) {
            (void)rtems_event_send(spelf_tid[i], SPELF_EV_WAKE);
        }
    }
}

void spelf_rtems_start(uint32_t initial_mode) {
    if (spelf_task_count > SPELF_MAX_TASKS) {
        SPELF_FATAL(0x06u);
    }
    for (uint32_t i = 0; i < spelf_task_count; i++) {
        (void)spelf_ticks(spelf_tasks[i].period_us);
        if (spelf_has_schedules()) {
            for (uint32_t k = 0; k < spelf_schedule_count; k++) {
                if (spelf_schedules[k].period_us[i] != 0u) {
                    (void)spelf_ticks(spelf_schedules[k].period_us[i]);
                }
            }
        }
    }
    if (spelf_has_schedules()) {
        if (&spelf_mode_count == NULL || initial_mode >= spelf_mode_count) {
            SPELF_FATAL(0x07u);
        }
        spelf_cur_sched = spelf_mode_schedule[initial_mode];
    }
    if (&spelf_isr_count != NULL) {
        for (uint32_t k = 0; k < spelf_isr_count; k++) {
            if (rtems_interrupt_handler_install((rtems_vector_number)spelf_isrs[k].vector, spelf_isrs[k].name,
                                                RTEMS_INTERRUPT_UNIQUE, spelf_isr_tramp, (void *)(uintptr_t)k)
                    != RTEMS_SUCCESSFUL) {
                SPELF_FATAL(0x08u);
            }
        }
    }
    for (uint32_t i = 0; i < spelf_task_count; i++) {
        const char *n = spelf_tasks[i].name;
        char c[4] = { ' ', ' ', ' ', ' ' };
        for (uint32_t j = 0; j < 4u && n[j] != '\0'; j++) {
            c[j] = n[j];
        }
        size_t st = (size_t)spelf_tasks[i].stack_bytes + SPELF_STACK_MARGIN;
        if (st < RTEMS_MINIMUM_STACK_SIZE) {
            st = RTEMS_MINIMUM_STACK_SIZE;
        }
        if (rtems_task_create(rtems_build_name(c[0], c[1], c[2], c[3]), spelf_prio(spelf_tasks[i].priority), st,
                              RTEMS_DEFAULT_MODES, RTEMS_DEFAULT_ATTRIBUTES | RTEMS_FLOATING_POINT, &spelf_tid[i])
                != RTEMS_SUCCESSFUL) {
            SPELF_FATAL(0x09u);
        }
    }
    for (uint32_t i = 0; i < spelf_task_count; i++) {
        if (rtems_task_start(spelf_tid[i], spelf_task_body, (rtems_task_argument)i) != RTEMS_SUCCESSFUL) {
            SPELF_FATAL(0x0Au);
        }
    }
}
