#ifndef SPELF_RTEMS_API_SUBSET_H
#define SPELF_RTEMS_API_SUBSET_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

typedef uint32_t rtems_id;
typedef uint32_t rtems_name;
typedef uint32_t rtems_task_priority;
typedef uintptr_t rtems_task_argument;
typedef void rtems_task;
typedef rtems_task (*rtems_task_entry)(rtems_task_argument);
typedef uint32_t rtems_interval;
typedef uint32_t rtems_mode;
typedef uint32_t rtems_attribute;
typedef uint32_t rtems_event_set;
typedef uint32_t rtems_option;
typedef uint32_t rtems_vector_number;
typedef uint32_t rtems_interrupt_level;
typedef void (*rtems_interrupt_handler)(void *);
typedef uintptr_t rtems_fatal_code;
typedef enum { RTEMS_FATAL_SOURCE_APPLICATION = 0 } rtems_fatal_source;
typedef enum {
  RTEMS_SUCCESSFUL = 0, RTEMS_TASK_EXITTED = 1, RTEMS_MP_NOT_CONFIGURED = 2, RTEMS_INVALID_NAME = 3,
  RTEMS_INVALID_ID = 4, RTEMS_TOO_MANY = 5, RTEMS_TIMEOUT = 6, RTEMS_OBJECT_WAS_DELETED = 7,
  RTEMS_INVALID_SIZE = 8, RTEMS_INVALID_ADDRESS = 9, RTEMS_INVALID_NUMBER = 10, RTEMS_NOT_DEFINED = 11
} rtems_status_code;

#define RTEMS_SELF ((rtems_id)0)
#define RTEMS_DEFAULT_MODES ((rtems_mode)0)
#define RTEMS_DEFAULT_ATTRIBUTES ((rtems_attribute)0)
#define RTEMS_FLOATING_POINT ((rtems_attribute)0x00000002)
#define RTEMS_MINIMUM_STACK_SIZE ((size_t)4096)
#define RTEMS_EVENT_0 ((rtems_event_set)0x00000001)
#define RTEMS_WAIT ((rtems_option)0x00000000)
#define RTEMS_EVENT_ANY ((rtems_option)0x00000002)
#define RTEMS_NO_TIMEOUT ((rtems_interval)0)
#define RTEMS_INTERRUPT_UNIQUE ((rtems_option)0x00000001)
#define rtems_build_name(c1, c2, c3, c4) \
  ((rtems_name)(((uint32_t)(uint8_t)(c1) << 24) | ((uint32_t)(uint8_t)(c2) << 16) | \
                ((uint32_t)(uint8_t)(c3) << 8) | (uint32_t)(uint8_t)(c4)))

rtems_status_code rtems_task_create(rtems_name name, rtems_task_priority initial_priority, size_t stack_size,
                                    rtems_mode initial_modes, rtems_attribute attribute_set, rtems_id *id);
rtems_status_code rtems_task_start(rtems_id id, rtems_task_entry entry_point, rtems_task_argument argument);
rtems_status_code rtems_task_set_priority(rtems_id id, rtems_task_priority new_priority,
                                          rtems_task_priority *old_priority);
rtems_status_code rtems_rate_monotonic_create(rtems_name name, rtems_id *id);
rtems_status_code rtems_rate_monotonic_period(rtems_id id, rtems_interval length);
rtems_status_code rtems_rate_monotonic_cancel(rtems_id id);
rtems_status_code rtems_event_send(rtems_id id, rtems_event_set event_in);
rtems_status_code rtems_event_receive(rtems_event_set event_in, rtems_option option_set, rtems_interval ticks,
                                      rtems_event_set *event_out);
rtems_interval rtems_clock_get_ticks_per_second(void);
rtems_status_code rtems_interrupt_handler_install(rtems_vector_number vector, const char *info,
                                                  rtems_option options, rtems_interrupt_handler routine, void *arg);
void rtems_cache_flush_multiple_data_lines(const void *begin, size_t size);
void rtems_cache_invalidate_multiple_data_lines(const void *begin, size_t size);
_Noreturn void rtems_fatal(rtems_fatal_source fatal_source, rtems_fatal_code fatal_code);
uint32_t spelf_shim_isr_disable(void);
void spelf_shim_isr_enable(uint32_t level);
#define rtems_interrupt_local_disable(_isr_cookie) do { (_isr_cookie) = spelf_shim_isr_disable(); } while (0)
#define rtems_interrupt_local_enable(_isr_cookie) spelf_shim_isr_enable(_isr_cookie)
#endif
