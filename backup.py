#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
backup.py v2.1
============================================================

自动备份目录到指定位置。

主要功能：
1. 源目录第一层普通文件 -> 一个 tar.gz
2. 源目录第一层子目录 -> 分别递归打包
3. 纯 Python 流式 tar.gz
4. 默认 1,000,000,000 bytes 自动分卷
5. .001 / .002 / .003 ...
6. 单卷自动使用 .tar.gz
7. 临时 .tmp 文件，成功后原子改名
8. 自动清理旧备份
9. 失败写入源目录 backup_error.log
10. 一个子目录失败后继续其他目录
11. 支持 exclude
12. 支持 dry-run
13. 支持 verify
14. 支持 flock 防止重复运行
15. 支持 nice
16. 启动前自动进行服务器环境检查
17. 环境不满足时输出对应安装/修复命令

兼容：
    CentOS Stream 9
    Python 3.9+
    Linux

注意：
    本脚本不依赖第三方 Python 包。
    tar/gzip 不属于运行时必须依赖。
"""

import argparse
import errno
import fcntl
import fnmatch
import gzip
import os
import platform
import secrets
import shutil
import signal
import stat
import string
import subprocess
import sys
import tarfile
import time
import traceback
import re
from datetime import datetime


VERSION = "2.1.0"

RANDOM_CHARS = string.ascii_letters + string.digits

DEFAULT_VOLUME_SIZE = 1000 ** 3
DEFAULT_COMPRESSLEVEL = 6
DEFAULT_KEEP = 7

ACTIVE_WRITERS = []
INTERRUPTED = False


# ============================================================
# 环境检查
# ============================================================

class EnvironmentCheck:
    """
    服务器环境检查。

    本脚本使用 Python 标准库，因此：
        tar
        gzip
        split

    都不是强制依赖。

    检查内容：

    1. Python 版本
    2. Linux
    3. Python 标准库
    4. fcntl
    5. 源目录
    6. 目标目录
    7. 目标目录可写
    8. 源目录可读
    9. 磁盘空间
    10. 文件名/路径基本能力
    """

    REQUIRED_PYTHON = (3, 9)

    def __init__(
        self,
        source=None,
        dest=None,
        volume_size=DEFAULT_VOLUME_SIZE,
    ):
        self.source = source
        self.dest = dest
        self.volume_size = volume_size

        self.errors = []
        self.warnings = []

    def error(self, message, command=None):
        self.errors.append(
            {
                "message": message,
                "command": command,
            }
        )

    def warning(self, message):
        self.warnings.append(message)

    def check_python(self):
        current = sys.version_info

        if current < self.REQUIRED_PYTHON:

            version = (
                f"{current.major}."
                f"{current.minor}."
                f"{current.micro}"
            )

            self.error(
                f"Python 版本过低: {version}，"
                f"需要 Python "
                f"{self.REQUIRED_PYTHON[0]}."
                f"{self.REQUIRED_PYTHON[1]}+",
                command=self.python_install_command(),
            )

    def python_install_command(self):
        """
        根据系统生成 Python 安装建议。
        """

        if shutil.which("dnf"):
            return (
                "dnf install -y python3"
            )

        if shutil.which("yum"):
            return (
                "yum install -y python3"
            )

        if shutil.which("apt-get"):
            return (
                "apt-get update && "
                "apt-get install -y python3"
            )

        if shutil.which("apk"):
            return (
                "apk add python3"
            )

        return (
            "请安装 Python 3.9 或更高版本"
        )

    def check_linux(self):

        if sys.platform != "linux":

            self.error(
                "当前操作系统不是 Linux。"
                "本生产版针对 CentOS Stream 9/Linux 设计。"
            )

    def check_stdlib(self):

        modules = [
            "argparse",
            "fcntl",
            "fnmatch",
            "gzip",
            "os",
            "secrets",
            "signal",
            "shutil",
            "stat",
            "string",
            "sys",
            "tarfile",
            "time",
            "traceback",
        ]

        missing = []

        for module in modules:

            try:
                __import__(module)
            except ImportError:
                missing.append(module)

        if missing:

            self.error(
                "Python 标准库模块缺失: "
                + ", ".join(missing)
            )

    def check_fcntl(self):

        try:
            import fcntl  # noqa
        except ImportError:

            self.error(
                "Python 不支持 fcntl，"
                "当前平台无法使用 Linux 文件锁。"
            )

    def check_source(self):

        if not self.source:
            return

        if not os.path.exists(
            self.source
        ):

            self.error(
                f"源目录不存在: {self.source}",
                command=(
                    f"mkdir -p "
                    f"{self.source}"
                ),
            )

            return

        if not os.path.isdir(
            self.source
        ):

            self.error(
                f"源路径不是目录: {self.source}"
            )

            return

        if not os.access(
            self.source,
            os.R_OK | os.X_OK,
        ):

            self.error(
                f"没有读取源目录的权限: "
                f"{self.source}",
                command=(
                    f"chmod u+rx "
                    f"{self.source}"
                ),
            )

    def check_dest(self):

        if not self.dest:
            return

        if not os.path.exists(
            self.dest
        ):

            parent = os.path.dirname(
                self.dest.rstrip(os.sep)
            )

            if not parent:
                parent = "/"

            if not os.path.isdir(parent):

                self.error(
                    f"目标目录和父目录都不存在: "
                    f"{self.dest}",
                    command=(
                        f"mkdir -p "
                        f"{self.dest}"
                    ),
                )

                return

            if not os.access(
                parent,
                os.W_OK | os.X_OK,
            ):

                self.error(
                    f"无法创建目标目录: "
                    f"{self.dest}",
                    command=(
                        f"mkdir -p "
                        f"{self.dest}"
                    ),
                )

                return

            try:
                os.makedirs(
                    self.dest,
                    exist_ok=True,
                )
            except OSError as e:

                self.error(
                    f"无法创建目标目录: "
                    f"{self.dest}: {e}",
                    command=(
                        f"mkdir -p "
                        f"{self.dest}"
                    ),
                )

                return

        if not os.path.isdir(
            self.dest
        ):

            self.error(
                f"目标路径不是目录: "
                f"{self.dest}"
            )

            return

        if not os.access(
            self.dest,
            os.W_OK | os.X_OK,
        ):

            self.error(
                f"没有目标目录写权限: "
                f"{self.dest}",
                command=(
                    f"chmod u+rwx "
                    f"{self.dest}"
                ),
            )

    def check_disk_space(self):

        if not self.dest:
            return

        try:

            usage = shutil.disk_usage(
                self.dest
            )

            free = usage.free

            # 注意：
            # 压缩后通常小于源目录。
            # 这里不是要求 free >= source size，
            # 只是进行安全提醒。

            if free < self.volume_size:

                self.warning(
                    "目标磁盘剩余空间小于一个分卷大小："
                    f"{human_size(free)}"
                    " < "
                    f"{human_size(self.volume_size)}"
                )

        except OSError as e:

            self.warning(
                f"无法获取目标磁盘空间: {e}"
            )

    def check_commands(self):

        # 本脚本不强制需要 tar/gzip。
        #
        # 但是检查它们是否存在，
        # 仅作为信息提示。

        if not shutil.which("tar"):

            self.warning(
                "系统未找到 tar。"
                "当前 Python backend 不受影响；"
                "如果以后启用 GNU tar backend，"
                "需要安装 tar。"
            )

        if not shutil.which("gzip"):

            self.warning(
                "系统未找到 gzip。"
                "当前 Python backend 不受影响；"
                "Python gzip 模块仍然可以工作。"
            )

    def check_python_memory(self):

        # 只是提示，不作为硬性要求。

        try:

            page_size = os.sysconf(
                "SC_PAGE_SIZE"
            )

            pages = os.sysconf(
                "SC_PHYS_PAGES"
            )

            memory = (
                page_size * pages
            )

            if memory < 512 * 1000 * 1000:

                self.warning(
                    "系统物理内存低于 512MB，"
                    "大规模目录压缩可能受到影响。"
                )

        except Exception:
            pass

    def run(self):

        print()
        print("=" * 72)
        print(
            f"服务器环境检查 v{VERSION}"
        )
        print("=" * 72)

        print(
            f"操作系统 : "
            f"{platform.system()} "
            f"{platform.release()}"
        )

        print(
            f"Python    : "
            f"{platform.python_version()}"
        )

        self.check_python()
        self.check_linux()
        self.check_stdlib()
        self.check_fcntl()
        self.check_source()
        self.check_dest()
        self.check_disk_space()
        self.check_commands()
        self.check_python_memory()

        if self.errors:

            print()
            print(
                "环境检查失败："
            )

            for item in self.errors:

                print(
                    f"\n[错误] "
                    f"{item['message']}"
                )

                if item.get("command"):

                    print(
                        "安装/修复命令:"
                    )

                    print(
                        "  "
                        + item["command"]
                    )

            print()
            print("=" * 72)

            return False

        if self.warnings:

            print()

            for warning in self.warnings:

                print(
                    f"[警告] {warning}"
                )

        print()
        print(
            "[OK] 环境检查通过"
        )

        print("=" * 72)

        return True


# ============================================================
# 基础工具
# ============================================================

def human_size(size):

    size = float(size)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
        "PB",
    ]

    for unit in units:

        if size < 1000:

            if unit == "B":
                return f"{int(size)} {unit}"

            return f"{size:.2f} {unit}"

        size /= 1000

    return f"{size:.2f} EB"


def format_seconds(seconds):

    seconds = int(seconds)

    h = seconds // 3600
    seconds %= 3600

    m = seconds // 60
    s = seconds % 60

    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"

    return f"{m:02d}:{s:02d}"


def random_code(length=6):

    return "".join(
        secrets.choice(RANDOM_CHARS)
        for _ in range(length)
    )


def safe_realpath(path):

    try:
        return os.path.realpath(path)
    except Exception:
        return os.path.abspath(path)


def path_is_inside(path, parent):

    path = safe_realpath(path)
    parent = safe_realpath(parent)

    try:
        return (
            os.path.commonpath(
                [path, parent]
            )
            == parent
        )

    except ValueError:
        return False


# ============================================================
# 日志
# ============================================================

def log_error(
    log_path,
    message,
    exc=None,
):

    try:

        parent = os.path.dirname(
            log_path
        )

        if parent:
            os.makedirs(
                parent,
                exist_ok=True,
            )

        with open(
            log_path,
            "a",
            encoding="utf-8",
        ) as f:

            f.write(
                "[{}] {}\n".format(
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    message,
                )
            )

            if exc:

                traceback.print_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    file=f,
                )

            f.write("\n")

    except Exception as e:

        print(
            f"无法写入日志 "
            f"{log_path}: {e}",
            file=sys.stderr,
        )


# ============================================================
# 排除规则
# ============================================================

class ExcludeMatcher:

    def __init__(
        self,
        patterns=None,
    ):

        self.patterns = []

        for pattern in patterns or []:

            pattern = pattern.strip()

            if not pattern:
                continue

            self.patterns.append(
                pattern.replace(
                    "\\",
                    "/",
                )
            )

    def match(
        self,
        relative_path,
    ):

        relative_path = (
            relative_path.replace(
                os.sep,
                "/",
            )
        )

        basename = os.path.basename(
            relative_path
        )

        for pattern in self.patterns:

            if fnmatch.fnmatch(
                relative_path,
                pattern,
            ):
                return True

            if fnmatch.fnmatch(
                basename,
                pattern,
            ):
                return True

            if "/" not in pattern:

                for part in relative_path.split(
                    "/"
                ):

                    if fnmatch.fnmatch(
                        part,
                        pattern,
                    ):
                        return True

        return False


def load_exclude_file(path):

    patterns = []

    if not path:
        return patterns

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            patterns.append(line)

    return patterns


# ============================================================
# SplitWriter
# ============================================================

class SplitWriter:

    def __init__(
        self,
        base_path,
        volume_size,
    ):

        self.base_path = base_path
        self.volume_size = volume_size

        self.volume_index = 0

        self.current = None
        self.current_size = 0
        self.total_size = 0

        self.temp_files = []
        self.final_files = []

        self.closed = False
        self.aborted = False

        ACTIVE_WRITERS.append(
            self
        )

        self._open_next()

    def _temp_path(
        self,
        index,
    ):

        return (
            f"{self.base_path}."
            f"{index:03d}.tmp"
        )

    def _final_path(
        self,
        index,
    ):

        return (
            f"{self.base_path}."
            f"{index:03d}"
        )

    def _open_next(self):

        if self.current:
            self.current.close()

        self.volume_index += 1

        temp_path = self._temp_path(
            self.volume_index
        )

        self.current = open(
            temp_path,
            "wb",
            buffering=1024 * 1024,
        )

        self.current_size = 0

        self.temp_files.append(
            temp_path
        )

    def write(self, data):

        if not data:
            return 0

        if self.aborted:
            raise RuntimeError(
                "SplitWriter 已经终止"
            )

        mv = memoryview(data)

        total_written = 0

        while len(mv) > 0:

            remaining = (
                self.volume_size
                - self.current_size
            )

            if remaining <= 0:

                self._open_next()

                remaining = (
                    self.volume_size
                )

            chunk = mv[:remaining]

            self.current.write(
                chunk
            )

            n = len(chunk)

            self.current_size += n
            self.total_size += n
            total_written += n

            mv = mv[n:]

        return total_written

    def flush(self):

        if self.current:
            self.current.flush()

    def tell(self):
        return self.total_size

    def _fsync(self):

        if not self.current:
            return

        self.current.flush()

        try:
            os.fsync(
                self.current.fileno()
            )
        except OSError:
            pass

    def close(self):

        if self.closed:
            return

        self._fsync()

        if self.current:

            self.current.close()
            self.current = None

        if len(self.temp_files) == 1:

            temp_path = (
                self.temp_files[0]
            )

            final_path = (
                self.base_path
            )

            os.replace(
                temp_path,
                final_path,
            )

            self.final_files = [
                final_path
            ]

        else:

            final_files = []

            for index, temp_path in enumerate(
                self.temp_files,
                start=1,
            ):

                final_path = (
                    self._final_path(
                        index
                    )
                )

                os.replace(
                    temp_path,
                    final_path,
                )

                final_files.append(
                    final_path
                )

            self.final_files = (
                final_files
            )

        self.closed = True

        if self in ACTIVE_WRITERS:
            ACTIVE_WRITERS.remove(
                self
            )

    def abort(self):

        if self.aborted:
            return

        self.aborted = True

        try:

            if self.current:
                self.current.close()

        except Exception:
            pass

        self.current = None

        paths = set()

        for path in self.temp_files:
            paths.add(path)

        for path in self.final_files:
            paths.add(path)

        for index in range(
            1,
            self.volume_index + 1,
        ):

            paths.add(
                self._temp_path(index)
            )

            paths.add(
                self._final_path(index)
            )

        for path in paths:

            try:
                os.remove(path)
            except OSError:
                pass

        self.temp_files = []
        self.final_files = []

        if self in ACTIVE_WRITERS:
            ACTIVE_WRITERS.remove(
                self
            )


# ============================================================
# 文件名
# ============================================================

def generate_base_path(
    directory,
    source_name,
    sub_name,
    timestamp,
):

    for _ in range(100):

        parts = [
            "path",
            source_name,
        ]

        if sub_name:
            parts.append(sub_name)

        parts.append(timestamp)
        parts.append(random_code())

        base_name = "_".join(parts)

        base_path = os.path.join(
            directory,
            base_name + ".tar.gz",
        )

        possible = [
            base_path,
            base_path + ".001",
            base_path + ".001.tmp",
        ]

        if not any(
            os.path.exists(path)
            for path in possible
        ):
            return base_path

    raise RuntimeError(
        "无法生成唯一备份文件名"
    )


# ============================================================
# 打包
# ============================================================

def pack_files(
    file_paths,
    base_path,
    volume_size,
    compresslevel,
    matcher,
):

    writer = SplitWriter(
        base_path,
        volume_size,
    )

    start = time.monotonic()

    try:

        with tarfile.open(
            fileobj=writer,
            mode="w|gz",
            compresslevel=compresslevel,
        ) as tar:

            for path in file_paths:

                name = os.path.basename(
                    path
                )

                if matcher.match(name):
                    continue

                tar.add(
                    path,
                    arcname=name,
                    recursive=False,
                )

        writer.close()

    except BaseException:

        writer.abort()

        raise

    return {
        "output_size": writer.total_size,
        "elapsed": (
            time.monotonic()
            - start
        ),
        "files": writer.final_files,
    }


def pack_dir(
    dir_path,
    base_path,
    volume_size,
    compresslevel,
    matcher,
):

    writer = SplitWriter(
        base_path,
        volume_size,
    )

    start = time.monotonic()

    try:

        arcname = os.path.basename(
            dir_path.rstrip(os.sep)
        )

        root_info = tar.gettarinfo(
            dir_path,
            arcname=arcname,
        )

        with tarfile.open(
            fileobj=writer,
            mode="w|gz",
            compresslevel=compresslevel,
        ) as tar:

            if not matcher.match(
                arcname
            ):

                tar.addfile(
                    root_info
                )

            for root, dirs, files in os.walk(
                dir_path,
                topdown=True,
                followlinks=False,
            ):

                filtered_dirs = []

                for dirname in dirs:

                    full_path = os.path.join(
                        root,
                        dirname,
                    )

                    rel_path = os.path.relpath(
                        full_path,
                        dir_path,
                    )

                    tar_name = (
                        arcname
                        + "/"
                        + rel_path.replace(
                            os.sep,
                            "/",
                        )
                    )

                    if matcher.match(
                        tar_name
                    ):
                        continue

                    filtered_dirs.append(
                        dirname
                    )

                dirs[:] = filtered_dirs

                for dirname in dirs:

                    full_path = os.path.join(
                        root,
                        dirname,
                    )

                    rel_path = os.path.relpath(
                        full_path,
                        dir_path,
                    )

                    tar_name = (
                        arcname
                        + "/"
                        + rel_path.replace(
                            os.sep,
                            "/",
                        )
                    )

                    info = tar.gettarinfo(
                        full_path,
                        arcname=tar_name,
                    )

                    if not matcher.match(
                        tar_name
                    ):
                        tar.addfile(info)

                for filename in files:

                    full_path = os.path.join(
                        root,
                        filename,
                    )

                    rel_path = os.path.relpath(
                        full_path,
                        dir_path,
                    )

                    tar_name = (
                        arcname
                        + "/"
                        + rel_path.replace(
                            os.sep,
                            "/",
                        )
                    )

                    if matcher.match(
                        tar_name
                    ):
                        continue

                    info = tar.gettarinfo(
                        full_path,
                        arcname=tar_name,
                    )

                    if info.isreg():

                        with open(
                            full_path,
                            "rb",
                        ) as f:

                            tar.addfile(
                                info,
                                fileobj=f,
                            )

                    else:

                        tar.addfile(info)

        writer.close()

    except BaseException:

        writer.abort()

        raise

    return {
        "output_size": writer.total_size,
        "elapsed": (
            time.monotonic()
            - start
        ),
        "files": writer.final_files,
    }


# ============================================================
# 清理旧备份
# ============================================================

BACKUP_RE = re.compile(
    r"^(.*)\.tar\.gz(?:\.\d{3,})?$"
)

TIMESTAMP_RE = re.compile(
    r"_(\d{8}_\d{6})_"
    r"([A-Za-z0-9]{6})$"
)


def cleanup_old_backups(
    directory,
    prefix,
    keep,
):

    if keep <= 0:
        return

    if not os.path.isdir(
        directory
    ):
        return

    groups = {}

    try:
        names = os.listdir(
            directory
        )
    except OSError:
        return

    for name in names:

        if not name.startswith(
            prefix
        ):
            continue

        match = BACKUP_RE.match(
            name
        )

        if not match:
            continue

        base = match.group(1)

        match2 = TIMESTAMP_RE.search(
            base
        )

        if not match2:
            continue

        timestamp = match2.group(1)

        groups.setdefault(
            base,
            {
                "timestamp": timestamp,
                "files": [],
            },
        )["files"].append(
            os.path.join(
                directory,
                name,
            )
        )

    if len(groups) <= keep:
        return

    sorted_groups = sorted(
        groups.items(),
        key=lambda item: (
            item[1]["timestamp"],
            item[0],
        ),
        reverse=True,
    )

    for old_base, info in sorted_groups[
        keep:
    ]:

        for path in info["files"]:

            try:

                os.remove(path)

                print(
                    f"清理旧备份: {path}"
                )

            except OSError as e:

                print(
                    f"清理旧备份失败: "
                    f"{path}: {e}",
                    file=sys.stderr,
                )


# ============================================================
# Verify
# ============================================================

def get_backup_parts(
    base_path
):

    if os.path.isfile(
        base_path
    ):
        return [base_path]

    parent = os.path.dirname(
        base_path
    )

    filename = os.path.basename(
        base_path
    )

    result = []

    try:
        names = os.listdir(
            parent
        )
    except OSError:
        return []

    pattern = re.compile(
        r"^"
        + re.escape(filename)
        + r"\.(\d{3,})$"
    )

    for name in names:

        match = pattern.match(
            name
        )

        if not match:
            continue

        result.append(
            (
                int(match.group(1)),
                os.path.join(
                    parent,
                    name,
                ),
            )
        )

    result.sort(
        key=lambda x: x[0]
    )

    return [
        path
        for _, path in result
    ]


class LimitedReader:

    def __init__(
        self,
        paths,
    ):

        self.paths = paths
        self.index = 0
        self.current = None

    def _open_next(self):

        if self.current:
            self.current.close()

        if self.index >= len(
            self.paths
        ):
            return False

        self.current = open(
            self.paths[self.index],
            "rb",
            buffering=1024 * 1024,
        )

        self.index += 1

        return True

    def read(
        self,
        size=-1,
    ):

        if size == 0:
            return b""

        chunks = []
        remaining = size

        while True:

            if self.current is None:

                if not self._open_next():
                    break

            if size < 0:

                data = self.current.read()

            else:

                data = self.current.read(
                    remaining
                )

            if data:

                chunks.append(data)

                if size >= 0:

                    remaining -= len(
                        data
                    )

                    if remaining <= 0:
                        break

                continue

            self.current.close()
            self.current = None

        return b"".join(chunks)

    def close(self):

        if self.current:

            self.current.close()
            self.current = None


def verify_backup(
    base_path
):

    parts = get_backup_parts(
        base_path
    )

    if not parts:

        raise RuntimeError(
            f"找不到备份文件: "
            f"{base_path}"
        )

    if len(parts) > 1:

        for index, path in enumerate(
            parts,
            start=1,
        ):

            expected = (
                f"{base_path}."
                f"{index:03d}"
            )

            if path != expected:

                raise RuntimeError(
                    f"分卷缺失: "
                    f"期望 {expected}"
                )

    print(
        f"验证备份: "
        f"{os.path.basename(base_path)}"
    )

    reader = LimitedReader(
        parts
    )

    try:

        with gzip.GzipFile(
            fileobj=reader,
            mode="rb",
        ) as gz:

            with tarfile.open(
                fileobj=gz,
                mode="r|",
            ) as tar:

                count = 0

                while True:

                    member = tar.next()

                    if member is None:
                        break

                    count += 1

        print(
            f"验证成功: "
            f"{count} 个 tar 项"
        )

    except Exception as e:

        raise RuntimeError(
            f"备份验证失败: {e}"
        )

    finally:

        reader.close()


# ============================================================
# Lock
# ============================================================

class FileLock:

    def __init__(
        self,
        path,
    ):

        self.path = path
        self.fp = None

    def acquire(self):

        parent = os.path.dirname(
            self.path
        )

        if parent:
            os.makedirs(
                parent,
                exist_ok=True,
            )

        self.fp = open(
            self.path,
            "w",
        )

        try:

            fcntl.flock(
                self.fp.fileno(),
                fcntl.LOCK_EX
                | fcntl.LOCK_NB,
            )

        except BlockingIOError:

            self.fp.close()
            self.fp = None

            return False

        self.fp.write(
            str(os.getpid())
        )

        self.fp.flush()

        return True

    def release(self):

        if self.fp:

            try:

                fcntl.flock(
                    self.fp.fileno(),
                    fcntl.LOCK_UN,
                )

            except Exception:
                pass

            try:
                self.fp.close()
            except Exception:
                pass

            self.fp = None


# ============================================================
# Signal
# ============================================================

def signal_handler(
    signum,
    frame,
):

    global INTERRUPTED

    INTERRUPTED = True

    print(
        "\n收到终止信号，"
        "正在清理当前备份...",
        file=sys.stderr,
    )

    for writer in list(
        ACTIVE_WRITERS
    ):

        try:
            writer.abort()
        except Exception:
            pass

    raise KeyboardInterrupt


# ============================================================
# nice
# ============================================================

def apply_nice(
    nice_value
):

    if nice_value is None:
        return

    if not hasattr(
        os,
        "nice",
    ):
        return

    try:

        os.nice(
            nice_value
        )

    except OSError as e:

        print(
            f"设置 nice 失败: {e}",
            file=sys.stderr,
        )


# ============================================================
# Scan
# ============================================================

def scan_source(
    source,
    dest,
    log_path,
):

    file_paths = []
    subdirs = []

    dest_real = safe_realpath(
        dest
    )

    log_real = safe_realpath(
        log_path
    )

    for entry in os.scandir(
        source
    ):

        entry_path = os.path.abspath(
            entry.path
        )

        if (
            safe_realpath(entry_path)
            == log_real
        ):
            continue

        if (
            safe_realpath(entry_path)
            == dest_real
        ):
            continue

        try:

            if entry.is_dir(
                follow_symlinks=False
            ):

                entry_real = (
                    safe_realpath(
                        entry.path
                    )
                )

                if path_is_inside(
                    dest_real,
                    entry_real,
                ):

                    print(
                        "跳过包含目标目录的子目录: "
                        f"{entry.path}"
                    )

                    continue

                subdirs.append(
                    entry.path
                )

            elif entry.is_file(
                follow_symlinks=False
            ):

                file_paths.append(
                    entry.path
                )

            elif entry.is_symlink():

                file_paths.append(
                    entry.path
                )

        except OSError as e:

            print(
                f"扫描失败: "
                f"{entry.path}: {e}",
                file=sys.stderr,
            )

    file_paths.sort()
    subdirs.sort()

    return file_paths, subdirs


# ============================================================
# Dry Run
# ============================================================

def print_dry_run(
    source,
    dest,
    file_paths,
    subdirs,
    matcher,
):

    print()
    print("=" * 72)
    print(
        "DRY-RUN：不会创建任何备份"
    )
    print("=" * 72)

    print(
        f"源目录   : {source}"
    )

    print(
        f"目标目录 : {dest}"
    )

    print()

    print(
        f"第一层文件: "
        f"{len(file_paths)} 个"
    )

    for path in file_paths:

        name = os.path.basename(
            path
        )

        if matcher.match(name):

            print(
                f"  [排除] {name}"
            )

        else:

            print(
                f"  [文件] {name}"
            )

    print()

    print(
        f"一级子目录: "
        f"{len(subdirs)} 个"
    )

    for path in subdirs:

        name = os.path.basename(
            path.rstrip(os.sep)
        )

        if matcher.match(name):

            print(
                f"  [排除] {name}"
            )

        else:

            print(
                f"  [目录] {name}"
            )

    print("=" * 72)


# ============================================================
# Parser
# ============================================================

def build_parser():

    parser = argparse.ArgumentParser(
        description=(
            "自动备份目录到指定位置 "
            f"(v{VERSION})"
        )
    )

    parser.add_argument(
        "--source",
        required=True,
        help="源目录",
    )

    parser.add_argument(
        "--dest",
        required=True,
        help="目标目录",
    )

    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=(
            "每个输出目录保留最近几份备份；"
            "0 表示不清理；默认 7"
        ),
    )

    parser.add_argument(
        "--volume-size",
        type=int,
        default=DEFAULT_VOLUME_SIZE,
        help=(
            "分卷大小（字节），"
            "默认 1000000000"
        ),
    )

    parser.add_argument(
        "--compresslevel",
        type=int,
        default=DEFAULT_COMPRESSLEVEL,
        choices=range(1, 10),
        help=(
            "gzip 压缩级别 1-9，默认 6"
        ),
    )

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help=(
            "排除规则，可重复使用。"
            "例如 --exclude cache "
            "--exclude '*.log'"
        ),
    )

    parser.add_argument(
        "--exclude-file",
        default=None,
        help=(
            "排除规则文件，每行一个规则"
        ),
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "备份完成后验证 tar.gz "
            "及全部分卷"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "只扫描并显示计划，"
            "不实际备份"
        ),
    )

    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "只检查服务器环境，"
            "不执行备份"
        ),
    )

    parser.add_argument(
        "--lock",
        default=None,
        help=(
            "锁文件路径；"
            "默认：目标目录/.backup.lock"
        ),
    )

    parser.add_argument(
        "--nice",
        type=int,
        default=None,
        help=(
            "调整进程 nice 值，例如 10"
        ),
    )

    parser.add_argument(
        "--log",
        default=None,
        help=(
            "失败日志路径；"
            "默认：源目录/backup_error.log"
        ),
    )

    return parser


# ============================================================
# Main
# ============================================================

def main():

    parser = build_parser()

    args = parser.parse_args()

    source = os.path.abspath(
        args.source
    )

    dest = os.path.abspath(
        args.dest
    )

    # --------------------------------------------
    # 环境检查
    # --------------------------------------------

    env = EnvironmentCheck(
        source=source,
        dest=dest,
        volume_size=args.volume_size,
    )

    if not env.run():
        return 2

    if args.check_only:

        print()
        print(
            "环境检查完成，"
            "未执行备份。"
        )

        return 0

    # --------------------------------------------
    # 参数检查
    # --------------------------------------------

    if args.volume_size <= 0:

        print(
            "--volume-size 必须大于 0",
            file=sys.stderr,
        )

        return 2

    if args.keep < 0:

        print(
            "--keep 不能小于 0",
            file=sys.stderr,
        )

        return 2

    # --------------------------------------------
    # exclude
    # --------------------------------------------

    if args.exclude_file:

        try:

            args.exclude.extend(
                load_exclude_file(
                    args.exclude_file
                )
            )

        except Exception as e:

            print(
                f"读取 exclude 文件失败: "
                f"{e}",
                file=sys.stderr,
            )

            return 2

    matcher = ExcludeMatcher(
        args.exclude
    )

    os.makedirs(
        dest,
        exist_ok=True,
    )

    log_path = (
        os.path.abspath(
            args.log
        )
        if args.log
        else os.path.join(
            source,
            "backup_error.log",
        )
    )

    lock_path = (
        os.path.abspath(
            args.lock
        )
        if args.lock
        else os.path.join(
            dest,
            ".backup.lock",
        )
    )

    # --------------------------------------------
    # Lock
    # --------------------------------------------

    lock = FileLock(
        lock_path
    )

    if not lock.acquire():

        print(
            "已有另一个备份任务正在运行，退出。",
            file=sys.stderr,
        )

        return 3

    # --------------------------------------------
    # Signal
    # --------------------------------------------

    signal.signal(
        signal.SIGINT,
        signal_handler,
    )

    signal.signal(
        signal.SIGTERM,
        signal_handler,
    )

    apply_nice(
        args.nice
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    source_name = (
        os.path.basename(
            source.rstrip(os.sep)
        )
        or "root"
    )

    prefix = (
        "path_"
        + source_name
        + "_"
    )

    errors = []

    success_count = 0
    fail_count = 0
    skipped_count = 0

    total_output_size = 0

    overall_start = (
        time.monotonic()
    )

    print()
    print("=" * 72)
    print(
        f" 自动备份 v{VERSION}"
    )
    print("=" * 72)

    print(
        f"源目录       : {source}"
    )

    print(
        f"目标目录     : {dest}"
    )

    print(
        f"保留备份     : {args.keep}"
    )

    print(
        f"分卷大小     : "
        f"{args.volume_size:,} bytes "
        f"({human_size(args.volume_size)})"
    )

    print(
        f"gzip 压缩级别: "
        f"{args.compresslevel}"
    )

    print(
        f"验证         : "
        f"{'是' if args.verify else '否'}"
    )

    if matcher.patterns:

        print(
            "排除规则     : "
            + ", ".join(
                matcher.patterns
            )
        )

    print("=" * 72)

    # --------------------------------------------
    # Scan
    # --------------------------------------------

    try:

        file_paths, subdirs = scan_source(
            source,
            dest,
            log_path,
        )

    except Exception as e:

        log_error(
            log_path,
            f"扫描源目录失败: {e}",
            e,
        )

        lock.release()

        return 1

    print(
        f"第一层文件   : "
        f"{len(file_paths)} 个"
    )

    print(
        f"一级子目录   : "
        f"{len(subdirs)} 个"
    )

    # --------------------------------------------
    # Dry Run
    # --------------------------------------------

    if args.dry_run:

        print_dry_run(
            source,
            dest,
            file_paths,
            subdirs,
            matcher,
        )

        lock.release()

        return 0

    # --------------------------------------------
    # 第一层文件
    # --------------------------------------------

    filtered_files = [
        path
        for path in file_paths
        if not matcher.match(
            os.path.basename(path)
        )
    ]

    if filtered_files:

        try:

            base_path = generate_base_path(
                dest,
                source_name,
                None,
                timestamp,
            )

            print()
            print(
                "[1] 打包第一层文件"
            )

            print(
                f"    文件数 : "
                f"{len(filtered_files)}"
            )

            print(
                f"    输出   : "
                f"{base_path}"
            )

            result = pack_files(
                filtered_files,
                base_path,
                args.volume_size,
                args.compresslevel,
                matcher,
            )

            elapsed = result[
                "elapsed"
            ]

            output_size = result[
                "output_size"
            ]

            total_output_size += (
                output_size
            )

            speed = (
                output_size / elapsed
                if elapsed > 0
                else 0
            )

            print(
                "    成功   : "
                f"{human_size(output_size)}"
                f" / {format_seconds(elapsed)}"
                f" / {human_size(speed)}/s"
            )

            if args.verify:

                verify_backup(
                    base_path
                )

            cleanup_old_backups(
                dest,
                prefix,
                args.keep,
            )

            success_count += 1

        except KeyboardInterrupt:

            lock.release()

            return 130

        except Exception as e:

            fail_count += 1
            errors.append(e)

            log_error(
                log_path,
                f"第一层文件打包失败: {e}",
                e,
            )

            print(
                f"    失败: {e}",
                file=sys.stderr,
            )

    else:

        print(
            "第一层文件没有需要备份的内容"
        )

        skipped_count += 1

    # --------------------------------------------
    # 一级子目录
    # --------------------------------------------

    total_tasks = len(subdirs)

    for index, subdir in enumerate(
        subdirs,
        start=1,
    ):

        if INTERRUPTED:
            break

        sub_name = os.path.basename(
            subdir.rstrip(os.sep)
        )

        print()
        print(
            f"[{index}/{total_tasks}] "
            f"打包目录: {sub_name}"
        )

        if matcher.match(
            sub_name
        ):

            print(
                "    [SKIP] "
                "被排除规则排除"
            )

            skipped_count += 1

            continue

        out_dir = os.path.join(
            dest,
            sub_name,
        )

        try:

            os.makedirs(
                out_dir,
                exist_ok=True,
            )

            base_path = generate_base_path(
                out_dir,
                source_name,
                sub_name,
                timestamp,
            )

            print(
                f"    源目录 : {subdir}"
            )

            print(
                f"    输出   : {base_path}"
            )

            result = pack_dir(
                subdir,
                base_path,
                args.volume_size,
                args.compresslevel,
                matcher,
            )

            elapsed = result[
                "elapsed"
            ]

            output_size = result[
                "output_size"
            ]

            total_output_size += (
                output_size
            )

            speed = (
                output_size / elapsed
                if elapsed > 0
                else 0
            )

            print(
                "    成功   : "
                f"{human_size(output_size)}"
                f" / {format_seconds(elapsed)}"
                f" / {human_size(speed)}/s"
            )

            if args.verify:

                verify_backup(
                    base_path
                )

            cleanup_old_backups(
                out_dir,
                prefix,
                args.keep,
            )

            success_count += 1

        except KeyboardInterrupt:

            lock.release()

            return 130

        except Exception as e:

            fail_count += 1
            errors.append(e)

            log_error(
                log_path,
                f"文件夹打包失败 "
                f"{subdir}: {e}",
                e,
            )

            print(
                f"    失败: {e}",
                file=sys.stderr,
            )

            continue

    # --------------------------------------------
    # Summary
    # --------------------------------------------

    elapsed_total = (
        time.monotonic()
        - overall_start
    )

    print()
    print("=" * 72)
    print("备份完成")
    print("=" * 72)

    print(
        f"成功         : "
        f"{success_count}"
    )

    print(
        f"失败         : "
        f"{fail_count}"
    )

    print(
        f"跳过         : "
        f"{skipped_count}"
    )

    print(
        f"输出大小     : "
        f"{human_size(total_output_size)}"
    )

    print(
        f"总耗时       : "
        f"{format_seconds(elapsed_total)}"
    )

    if fail_count:

        print(
            f"错误日志     : "
            f"{log_path}"
        )

    print("=" * 72)

    lock.release()

    if fail_count:
        return 1

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        for writer in list(
            ACTIVE_WRITERS
        ):

            try:
                writer.abort()
            except Exception:
                pass

        print(
            "\n备份已中断。",
            file=sys.stderr,
        )

        sys.exit(130)

    except Exception as e:

        for writer in list(
            ACTIVE_WRITERS
        ):

            try:
                writer.abort()
            except Exception:
                pass

        print(
            f"严重错误: {e}",
            file=sys.stderr,
        )

        traceback.print_exc()

        sys.exit(1)
