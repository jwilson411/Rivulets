"""Windows sandbox backend for the Code Execution tool (NFR-3.5, ADR-008, #515).

The mechanism is an **AppContainer** (the OS sandbox Chromium, Firefox, and
MSIX-packaged apps run under), not a bare job object — ADR-008 originally
proposed job objects alone, but a job object only bounds process/resource
usage; it restricts neither filesystem nor network access, so it can't meet
NFR-3.5's MUST requirements by itself. An AppContainer can:

  - **Filesystem**: an AppContainer process passes an access check only if
    the resource's ACL explicitly grants its AppContainer SID (or the
    `ALL APPLICATION PACKAGES` group, which Windows grants read+execute on
    system locations like %SystemRoot% and %ProgramFiles% by default).
    User-profile paths carry no such ACE, so the workspace directory (the
    SQLite DB, credential fallback store) and dotfile credential stores
    (~/.ssh and friends) are unreadable and unwritable by default — a
    *stronger* read posture than the macOS backend's best-effort deny list.
    This module grants the sandbox SID exactly two carve-outs at setup:
    modify rights on the per-workspace sandbox directory (the same
    `<workspace_dir>/tool_code_exec` root the other backends confine writes
    to), and read+execute on the Python installation itself (sys.prefix /
    sys.base_prefix) so the interpreter can start — mirroring the other
    backends' "reads of the interpreter/stdlib stay open" posture.
  - **Network**: the Windows Filtering Platform denies an AppContainer all
    network access unless the process was created with explicit network
    *capabilities*. Deny-by-default (NFR-3.5) is therefore the zero-config
    state: no capabilities are passed. When the workspace opts in via
    RIVULETS_CODE_EXEC_NETWORK_ACCESS=1, the `internetClient`,
    `internetClientServer`, and `privateNetworkClientServer` capability
    SIDs are attached. (Loopback stays blocked either way — AppContainer
    loopback needs an admin-granted exemption Windows reserves for
    debugging; the test-suite network probes account for this.)

A job object still participates, for the piece it *is* good at: lifetime
control. The child starts CREATE_SUSPENDED, is assigned to a job with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, and only then resumes — so a timeout
(or this process dying) reliably tears down the whole process tree via
TerminateJobObject/handle close, with no orphan-grandchild escape between
spawn and assignment.

Everything here is stdlib ctypes against documented Win32 APIs (userenv,
kernelbase, kernel32, advapi32) — no new dependencies. The module is
importable on every platform (pyright checks it under pythonPlatform
"All"); the Win32 machinery only exists inside the `sys.platform ==
"win32"` branch, and the non-Windows fallbacks report unsupported/raise.

Verified in CI on a real windows-latest runner (ci.yml `test-server-windows`),
not just unit-mocked — see tests/test_code_exec_tool.py.
"""

import subprocess
import sys
from pathlib import Path

_APP_CONTAINER_NAME = "rivulets-code-exec"
_APP_CONTAINER_DISPLAY = "Rivulets Code Execution sandbox"
_NETWORK_CAPABILITY_NAMES = (
    "internetClient",
    "internetClientServer",
    "privateNetworkClientServer",
)

if sys.platform == "win32":
    import ctypes
    import functools
    import msvcrt
    import os
    import shutil
    import tempfile
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _userenv = ctypes.WinDLL("userenv", use_last_error=True)

    _S_OK = 0
    _HRESULT_ALREADY_EXISTS = -2147024713  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
    _SE_GROUP_ENABLED = 0x00000004
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_NO_WINDOW = 0x08000000
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _STARTF_USESTDHANDLES = 0x00000100
    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _WAIT_TIMEOUT = 0x00000102

    class _SidAndAttributes(ctypes.Structure):
        Sid: int | None
        Attributes: int
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _SecurityCapabilities(ctypes.Structure):
        AppContainerSid: "ctypes.c_void_p | int | None"
        Capabilities: "ctypes.Array[_SidAndAttributes] | None"
        CapabilityCount: int
        _fields_ = [
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.POINTER(_SidAndAttributes)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    class _StartupInfoW(ctypes.Structure):
        cb: int
        dwFlags: int
        hStdInput: int | None
        hStdOutput: int | None
        hStdError: int | None
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _StartupInfoExW(ctypes.Structure):
        StartupInfo: _StartupInfoW
        lpAttributeList: "ctypes.c_void_p | int | None"
        _fields_ = [
            ("StartupInfo", _StartupInfoW),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    class _ProcessInformation(ctypes.Structure):
        hProcess: int | None
        hThread: int | None
        dwProcessId: int
        dwThreadId: int
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class _JobBasicLimits(ctypes.Structure):
        LimitFlags: int
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JobExtendedLimits(ctypes.Structure):
        BasicLimitInformation: _JobBasicLimits
        _fields_ = [
            ("BasicLimitInformation", _JobBasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    # Explicit prototypes for everything that passes or returns a HANDLE or
    # pointer-sized value: ctypes's default conversion is c_int (32-bit),
    # and while Win64 kernel handles are documented to stay 32-bit-safe,
    # relying on that implicitly is exactly the kind of latent truncation
    # bug this file shouldn't carry. Wrapped in a function so a trimmed
    # Windows build missing any of these exports (WinDLL attribute lookup
    # is GetProcAddress → AttributeError) degrades to is_supported() False
    # instead of an import-time crash of the whole tools package.
    def _configure_prototypes() -> bool:
        try:
            _configure_prototypes_unchecked()
        except AttributeError:
            return False
        return True

    def _configure_prototypes_unchecked() -> None:
        _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        _kernel32.SetInformationJobObject.restype = wintypes.BOOL
        _kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        _kernel32.TerminateJobObject.restype = wintypes.BOOL
        _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        _kernel32.TerminateProcess.restype = wintypes.BOOL
        _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        _kernel32.ResumeThread.restype = wintypes.DWORD
        _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
        _kernel32.WaitForSingleObject.restype = wintypes.DWORD
        _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        _kernel32.LocalFree.restype = ctypes.c_void_p
        _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
        _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        _kernel32.InitializeProcThreadAttributeList.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        )
        _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        _kernel32.UpdateProcThreadAttribute.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,  # DWORD_PTR Attribute
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        _kernel32.DeleteProcThreadAttributeList.restype = None
        _kernel32.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
        _kernel32.CreateProcessW.restype = wintypes.BOOL
        _kernel32.CreateProcessW.argtypes = (
            wintypes.LPCWSTR,  # lpApplicationName
            wintypes.LPWSTR,  # lpCommandLine (mutable)
            ctypes.c_void_p,  # lpProcessAttributes
            ctypes.c_void_p,  # lpThreadAttributes
            wintypes.BOOL,  # bInheritHandles
            wintypes.DWORD,  # dwCreationFlags
            ctypes.c_void_p,  # lpEnvironment
            wintypes.LPCWSTR,  # lpCurrentDirectory
            ctypes.c_void_p,  # lpStartupInfo (STARTUPINFOEXW here)
            ctypes.POINTER(_ProcessInformation),
        )
        _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        _advapi32.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        )
        _userenv.CreateAppContainerProfile.restype = ctypes.c_int32  # HRESULT
        _userenv.CreateAppContainerProfile.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(_SidAndAttributes),
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_int32  # HRESULT
        _userenv.DeriveAppContainerSidFromAppContainerName.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
        )
        _kernelbase.DeriveCapabilitySidsFromName.restype = wintypes.BOOL
        _kernelbase.DeriveCapabilitySidsFromName.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
            ctypes.POINTER(wintypes.DWORD),
        )

    _APIS_PRESENT = _configure_prototypes()

    def _win_error(what: str) -> OSError:
        err = ctypes.WinError(ctypes.get_last_error())
        err.strerror = f"{what}: {err.strerror}"
        return err

    def is_supported() -> bool:
        """Whether this Windows install has everything the backend needs.

        All of it ships with Windows 10+ (the userenv AppContainer profile
        APIs, kernelbase's name-based capability-SID derivation, and icacls
        for the two ACL carve-outs), so in practice this is True on every
        OS version the release matrix targets — but each piece is probed
        rather than assumed (_APIS_PRESENT is the prototype-configuration
        pass, which touches every export this module calls), so an
        exotic/trimmed-down install degrades to "tool unavailable" instead
        of failing mid-run.
        """
        return _APIS_PRESENT and shutil.which("icacls") is not None

    def _sid_to_string(sid: "ctypes.c_void_p") -> str:
        out = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(out)):
            raise _win_error("ConvertSidToStringSidW")
        try:
            value = out.value
            assert value is not None
            return value
        finally:
            _kernel32.LocalFree(ctypes.cast(out, ctypes.c_void_p))

    @functools.cache
    def _app_container_sid() -> "ctypes.c_void_p":
        """Create (or reuse) the app's AppContainer profile and return its SID.

        The profile is a per-user registry entity keyed by name; creating
        it needs no elevation. It's deliberately stable across runs — the
        ACL carve-outs granted to its SID stay valid with it. Cached for
        the process lifetime; the one SID allocation is reused for every
        run and never freed.
        """
        sid = ctypes.c_void_p()
        hr = _userenv.CreateAppContainerProfile(
            _APP_CONTAINER_NAME,
            _APP_CONTAINER_DISPLAY,
            _APP_CONTAINER_DISPLAY,
            None,
            0,
            ctypes.byref(sid),
        )
        if hr == _S_OK:
            return sid
        if hr != _HRESULT_ALREADY_EXISTS:
            raise OSError(f"CreateAppContainerProfile failed (HRESULT {hr:#010x})")
        hr = _userenv.DeriveAppContainerSidFromAppContainerName(
            _APP_CONTAINER_NAME, ctypes.byref(sid)
        )
        if hr != _S_OK:
            raise OSError(f"DeriveAppContainerSidFromAppContainerName failed (HRESULT {hr:#010x})")
        return sid

    @functools.cache
    def _network_capabilities() -> "ctypes.Array[_SidAndAttributes]":
        """SID_AND_ATTRIBUTES array for the opt-in network capabilities.

        Derived by *name* (DeriveCapabilitySidsFromName) rather than
        hard-coded WELL_KNOWN_SID_TYPE ordinals — the names are the
        documented stable identifiers (the same strings an appx manifest
        uses). Cached for the process lifetime; the handful of SIDs the OS
        allocates here are reused for every network-enabled run.
        """
        sids: list[int] = []
        for name in _NETWORK_CAPABILITY_NAMES:
            group_sids = ctypes.POINTER(ctypes.c_void_p)()
            group_count = wintypes.DWORD()
            cap_sids = ctypes.POINTER(ctypes.c_void_p)()
            cap_count = wintypes.DWORD()
            ok = _kernelbase.DeriveCapabilitySidsFromName(
                name,
                ctypes.byref(group_sids),
                ctypes.byref(group_count),
                ctypes.byref(cap_sids),
                ctypes.byref(cap_count),
            )
            if not ok:
                raise _win_error(f"DeriveCapabilitySidsFromName({name})")
            for i in range(cap_count.value):
                # Indexing POINTER(c_void_p) auto-converts to int | None.
                sid = cap_sids[i]
                assert sid is not None
                sids.append(sid)
        arr = (_SidAndAttributes * len(sids))()
        for i, sid_value in enumerate(sids):
            arr[i].Sid = sid_value
            arr[i].Attributes = _SE_GROUP_ENABLED
        return arr

    @functools.cache
    def _grant_access(path: str, spec: str) -> None:
        """Grant the AppContainer SID an ACL carve-out on `path` via icacls.

        Inheritable ((OI)(CI)) so files created under the directory later —
        the per-run script and output files — are covered, and idempotent
        (icacls re-granting an identical ACE is a no-op); caching per
        (path, spec) just avoids repeated subprocess round-trips.
        """
        sid_str = _sid_to_string(_app_container_sid())
        icacls = shutil.which("icacls")
        assert icacls is not None  # is_supported() already confirmed this
        # Trusted-inputs note (same as code_exec._run_macos): every argument
        # here is built by this module — agent-submitted code never reaches
        # an icacls argument.
        result = subprocess.run(  # noqa: S603
            [icacls, path, "/grant", f"*{sid_str}:{spec}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise OSError(
                f"icacls failed granting {spec} on {path} (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def _grant_sandbox_carve_outs(sandbox_dir: Path) -> None:
        # Modify (read/write/create/delete) inside the sandbox root only.
        _grant_access(str(sandbox_dir.resolve()), "(OI)(CI)(M)")
        # Read+execute on the Python installation so the interpreter can
        # start: sys.prefix covers a venv's python.exe, sys.base_prefix the
        # stdlib underneath it (identical for a non-venv install; the
        # _grant_access cache collapses the duplicate).
        for prefix in {str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())}:
            _grant_access(prefix, "(OI)(CI)(RX)")

    def _environment_block(sandbox_dir: Path) -> "ctypes.Array[ctypes.c_wchar]":
        # Parent env inherited, matching the other backends' subprocess.run
        # behavior; TEMP/TMP are pointed inside the sandbox so tempfile
        # works in the child, and PYTHONIOENCODING pins the encoding this
        # module reads the output files back with.
        env = {
            **os.environ,
            "TEMP": str(sandbox_dir),
            "TMP": str(sandbox_dir),
            "PYTHONIOENCODING": "utf-8",
        }
        block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
        buf = (ctypes.c_wchar * len(block))()
        buf[:] = block
        return buf

    def _make_job_object() -> int:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _win_error("CreateJobObjectW")
        limits = _JobExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            _kernel32.CloseHandle(job)
            raise _win_error("SetInformationJobObject")
        return int(job)

    def _inheritable_output_file(sandbox_dir: Path, tag: str) -> tuple[int, Path]:
        fd, name = tempfile.mkstemp(prefix=f"__code_exec_{tag}_", dir=sandbox_dir)
        os.set_handle_inheritable(msvcrt.get_osfhandle(fd), True)
        return fd, Path(name)

    def run_sandboxed(
        script_path: Path, sandbox_dir: Path, allow_network: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        """Run `python script_path` inside the AppContainer, mirroring the
        subprocess.run(capture_output=True, text=True, timeout=...) contract
        the firejail/sandbox-exec backends return (including raising
        subprocess.TimeoutExpired on timeout)."""
        _grant_sandbox_carve_outs(sandbox_dir)

        cmd = [sys.executable, str(script_path)]
        cmd_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
        env_block = _environment_block(sandbox_dir)

        caps = _SecurityCapabilities()
        caps.AppContainerSid = _app_container_sid()
        if allow_network:
            cap_array = _network_capabilities()
            caps.Capabilities = cap_array
            caps.CapabilityCount = len(cap_array)

        # One attribute (the security capabilities) on the thread-attribute
        # list: sized with the documented probe call, then initialized.
        attr_size = ctypes.c_size_t()
        _kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_size))
        attr_buf = ctypes.create_string_buffer(attr_size.value)
        if not _kernel32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(attr_size)):
            raise _win_error("InitializeProcThreadAttributeList")

        job: int | None = None
        proc_info = _ProcessInformation()
        out_fd, out_path = _inheritable_output_file(sandbox_dir, "stdout")
        err_fd, err_path = _inheritable_output_file(sandbox_dir, "stderr")
        nul_fd = os.open(os.devnull, os.O_RDONLY)
        os.set_handle_inheritable(msvcrt.get_osfhandle(nul_fd), True)
        try:
            if not _kernel32.UpdateProcThreadAttribute(
                attr_buf,
                0,
                _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(caps),
                ctypes.sizeof(caps),
                None,
                None,
            ):
                raise _win_error("UpdateProcThreadAttribute")

            startup = _StartupInfoExW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = msvcrt.get_osfhandle(nul_fd)
            startup.StartupInfo.hStdOutput = msvcrt.get_osfhandle(out_fd)
            startup.StartupInfo.hStdError = msvcrt.get_osfhandle(err_fd)
            startup.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

            job = _make_job_object()

            # CREATE_SUSPENDED -> assign to the kill-on-close job -> resume:
            # the child can't spawn anything before it's inside the job, so
            # there's no window where a grandchild could outlive a timeout
            # kill (the CREATE_SUSPENDED + SetInformationJobObject
            # sequencing #515's acceptance criteria call for).
            ok = _kernel32.CreateProcessW(
                None,
                cmd_line,
                None,
                None,
                True,  # inherit handles (the three std handles above)
                _CREATE_SUSPENDED
                | _CREATE_NO_WINDOW
                | _CREATE_UNICODE_ENVIRONMENT
                | _EXTENDED_STARTUPINFO_PRESENT,
                env_block,
                str(sandbox_dir),
                ctypes.byref(startup),
                ctypes.byref(proc_info),
            )
            if not ok:
                raise _win_error("CreateProcessW (AppContainer)")

            if not _kernel32.AssignProcessToJobObject(job, proc_info.hProcess):
                _kernel32.TerminateProcess(proc_info.hProcess, 1)
                raise _win_error("AssignProcessToJobObject")
            _kernel32.ResumeThread(proc_info.hThread)

            wait = _kernel32.WaitForSingleObject(proc_info.hProcess, timeout * 1000)
            if wait == _WAIT_TIMEOUT:
                _kernel32.TerminateJobObject(job, 1)
                _kernel32.WaitForSingleObject(proc_info.hProcess, 5000)
                raise subprocess.TimeoutExpired(cmd, timeout)

            exit_code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(proc_info.hProcess, ctypes.byref(exit_code)):
                raise _win_error("GetExitCodeProcess")

            os.close(out_fd)
            os.close(err_fd)
            out_fd = err_fd = -1
            stdout = out_path.read_text(encoding="utf-8", errors="replace")
            stderr = err_path.read_text(encoding="utf-8", errors="replace")
            # DWORD exit codes are unsigned; fold e.g. STATUS_ACCESS_VIOLATION
            # into the negative form CompletedProcess conventionally carries.
            code = exit_code.value if exit_code.value < 1 << 31 else exit_code.value - (1 << 32)
            return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)
        finally:
            for fd in (out_fd, err_fd, nul_fd):
                if fd >= 0:
                    os.close(fd)
            for handle in (proc_info.hThread, proc_info.hProcess, job):
                if handle:
                    _kernel32.CloseHandle(handle)
            _kernel32.DeleteProcThreadAttributeList(attr_buf)
            for leftover in (out_path, err_path):
                leftover.unlink(missing_ok=True)

else:

    def is_supported() -> bool:
        """Windows-only backend; never supported elsewhere (Linux/macOS have
        their own backends in code_exec.py)."""
        return False

    def run_sandboxed(
        script_path: Path, sandbox_dir: Path, allow_network: bool, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("the Windows sandbox backend only runs on Windows")
