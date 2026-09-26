"""Linux child launcher: release owned GPU processes when their parent dies."""

import ctypes
import os
import signal
import sys

parent = int(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG)")
if os.getppid() != parent:
    raise SystemExit(0)
os.execvp(sys.argv[2], sys.argv[2:])
