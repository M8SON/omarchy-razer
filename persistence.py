#!/usr/bin/env python3
"""Set restore_persistence = True in ~/.config/openrazer/razer.conf.

Run by setup.sh. The daemon writes persistence.conf on exit either way, but
only reads it back at startup when this is True -- which is why lights come
back dead after a reboot on a default install.

razer.conf sits at a predictable path under $HOME, so this never trusts a
check it made by name. The config directory is opened once, and every later
open, read, create and rename goes through that descriptor with O_NOFOLLOW,
so a symlink or a swapped file cannot slip in between the check and the use.
The new content lands in a fresh O_EXCL temp file in the same directory and
is renamed into place: the config is replaced atomically, never edited in
place.

    python3 persistence.py [config-dir]     (default ~/.config/openrazer)
"""

import errno
import os
import stat
import sys

NAME = "razer.conf"
MAX_CONF_BYTES = 64 * 1024
WANT = "restore_persistence = True"


class Refused(Exception):
    pass


def read_conf(dfd):
    """The current file's bytes, or None if there is none yet."""
    try:
        fd = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        # ELOOP: the name is a symlink. Say so rather than write through it.
        if exc.errno == errno.ELOOP:
            raise Refused("%s is a symlink" % NAME)
        raise Refused("cannot open %s: %s" % (NAME, exc.strerror))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused("%s is not a regular file" % NAME)
        if st.st_uid != os.geteuid():
            raise Refused("%s is not owned by you" % NAME)
        if st.st_size > MAX_CONF_BYTES:
            raise Refused("%s is too large to be a razer.conf" % NAME)
        # Regular files never block on read; drop O_NONBLOCK now that the
        # type is proven so a slow filesystem cannot surface EAGAIN.
        os.set_blocking(fd, True)
        chunks = []
        remaining = MAX_CONF_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_CONF_BYTES:
        raise Refused("%s is too large to be a razer.conf" % NAME)
    return data


def rewrite(text):
    """The text with restore_persistence forced True, or None if already."""
    lines = text.splitlines(True)
    if any(line.rstrip("\r\n") == WANT for line in lines):
        return None
    if any(line.startswith("restore_persistence") for line in lines):
        return "".join(WANT + "\n" if line.startswith("restore_persistence")
                       else line for line in lines)
    if not text:
        return "[Startup]\n" + WANT + "\n"
    if not text.endswith("\n"):
        text += "\n"
    return text + "\n[Startup]\n" + WANT + "\n"


def write_conf(dfd, text):
    tmp = ".%s.%s" % (NAME, os.urandom(6).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | os.O_CLOEXEC, 0o644, dir_fd=dfd)
    try:
        data = text.encode("utf-8")
        while data:
            n = os.write(fd, data)
            data = data[n:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.rename(tmp, NAME, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise


def enable_persistence(conf_dir):
    """Returns "already" or "updated"; raises Refused rather than guess."""
    try:
        os.makedirs(conf_dir, exist_ok=True)
    except OSError as exc:
        raise Refused("cannot create %s: %s" % (conf_dir, exc.strerror))
    try:
        dfd = os.open(conf_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                      | os.O_CLOEXEC)
    except OSError as exc:
        # O_DIRECTORY|O_NOFOLLOW on a symlink fails with ENOTDIR on Linux; the
        # descriptor open is what refuses, the lstat only names the reason.
        if os.path.islink(conf_dir):
            raise Refused("%s is a symlink" % conf_dir)
        raise Refused("cannot open %s: %s" % (conf_dir, exc.strerror))
    try:
        if os.fstat(dfd).st_uid != os.geteuid():
            raise Refused("%s is not owned by you" % conf_dir)
        data = read_conf(dfd)
        text = "" if data is None else data.decode("utf-8", "replace")
        new = rewrite(text)
        if new is None:
            return "already"
        write_conf(dfd, new)
        return "updated"
    finally:
        os.close(dfd)


def main():
    conf_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser("~"), ".config", "openrazer")
    try:
        result = enable_persistence(conf_dir)
    except Refused as exc:
        print("refusing: %s" % exc, file=sys.stderr)
        sys.exit(1)
    print("restore_persistence already True" if result == "already"
          else "restore_persistence set to True")


if __name__ == "__main__":
    main()
