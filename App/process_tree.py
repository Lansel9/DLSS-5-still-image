"""Keep the worker's GPU children in a Windows job, including on forced stop."""
import os

_worker_job = None


def protect_child_processes():
    # Retain the handle until OS process teardown: closing it would also kill
    # this worker, which is deliberately a member of the same job.
    global _worker_job
    if os.name != 'nt' or _worker_job is not None:
        return
    import ctypes as c
    from ctypes import wintypes as w

    class BasicLimits(c.Structure):
        _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                    ('flags', w.DWORD), ('min_working_set', c.c_size_t),
                    ('max_working_set', c.c_size_t), ('active_processes', w.DWORD),
                    ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class ExtendedLimits(c.Structure):
        _fields_ = [('basic', BasicLimits), ('io', c.c_uint64 * 6),
                    ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                    ('peak_process_memory', c.c_size_t), ('peak_job_memory', c.c_size_t)]

    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise c.WinError(c.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)):
        error = c.get_last_error()
        kernel.CloseHandle(handle)
        raise c.WinError(error)
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = c.get_last_error()
        kernel.CloseHandle(handle)
        raise c.WinError(error)
    _worker_job = handle
