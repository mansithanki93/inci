"""Single-process guard for one Inci account/subaccount."""
import fcntl
import os


class ProcessLockError(Exception):
    pass


class ProcessLock:
    def __init__(self, path):
        if not os.path.isabs(path):
            raise ValueError("process lock path must be absolute")
        self.path = path
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            handle.close()
            raise ProcessLockError(
                f"another Inci process holds {self.path}") from e
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self.handle = handle

    def release(self):
        if self.handle is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
