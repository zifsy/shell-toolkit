#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
backup.py v2.2.3
============================================================
Linux / CentOS Stream 9 / Python 3.9+

主要功能：
1. 源目录第一层普通文件 -> 一个 tar.gz
2. 源目录第一层子目录 -> 分别递归打包
3. 纯 Python 流式 tar.gz
4. 默认 1,000,000,000 bytes 自动分卷
5. 单卷：xxx.tar.gz
6. 多卷：xxx.tar.gz.001 / .002 / ...
7. 临时 .tmp 文件，成功后原子改名
8. 失败/中断自动删除本次产生的全部备份残留
9. verify 支持单卷和多卷，并检查分卷连续性
10. 磁盘空间安全检查
11. 自动清理旧备份
12. exclude / exclude-file
13. dry-run / check-only
14. flock 防止重复运行
15. nice
16. SIGINT/SIGTERM 安全清理
17. 不依赖第三方 Python 包

v2.2.2 修复:
- 修复 tarfile.gettarinfo() 在遇到 FIFO / Unix socket
  等无法归档的条目时返回 None 导致崩溃的问题
  (常见于 X11 / pulse / pipewire / dbus 运行时文件)
- 清理 remove_quietly() 冗余分支

v2.2.3 变更:
- 默认锁文件改为放在 /tmp 目录下，
  命名格式：<备份目录>_<接收目录>.backup.lock
  例如：/tmp/opt_wechat-selkies_www_backup.backup.lock
  （路径分隔符 / 会被替换为 _，非安全字符替换为 _）
  这样可以避免在目标目录留下锁文件，
  同时同一组 source/dest 仍然互斥。
- --lock 参数依然可用，显式指定时优先生效。

============================================================
运行示例
============================================================

1) 环境自检（不执行备份）

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --check-only

2) 预览（dry-run，不产生任何文件）

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --dry-run

3) 常规备份（分卷 1 GB，压缩级别 6，保留最近 7 份）

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --keep 7 \
        --volume-size 1000000000 \
        --compresslevel 6

4) 备份并验证所有分卷

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --verify

5) 排除运行态文件 / 缓存 / 日志
    （推荐用于 X11 / PulseAudio / PipeWire / D-Bus 场景）

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --exclude '*.sock' \
        --exclude '*.pid' \
        --exclude '.X11-unix' \
        --exclude 'pulse' \
        --exclude 'pipewire-*' \
        --exclude '*.log'

6) 使用排除规则文件（每行一个规则，# 开头为注释）

    # /etc/backup.exclude
    # *.log
    # cache
    # *.sock
    # .X11-unix
    # pulse
    # pipewire-*

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --exclude-file /etc/backup.exclude

7) 只保留最近 3 份，使用较低 CPU 优先级

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --keep 3 \
        --nice 10

8) 自定义锁文件和日志路径
    （默认锁文件已改为 /tmp 下的 source_dest.backup.lock）

    python3 backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --volume-size 500000000 \
        --lock /var/run/backup.lock \
        --log  /var/log/backup_error.log

9) 定时任务示例（crontab，每天 03:30 执行）

    30 3 * * * /usr/bin/python3 /usr/local/bin/backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --keep 7 \
        --nice 10 \
        --verify \
        --exclude '*.sock' \
        --exclude '*.pid' \
        >/dev/null 2>&1

10) 系统 systemd timer 示例

    # /etc/systemd/system/backup.service
    [Unit]
    Description=Automatic Backup

    [Service]
    Type=oneshot
    ExecStart=/usr/bin/python3 /usr/local/bin/backup.py \
        --source /opt/wechat-selkies \
        --dest   /www/backup \
        --keep 7 \
        --verify

    # /etc/systemd/system/backup.timer
    [Unit]
    Description=Run Automatic Backup daily

    [Timer]
    OnCalendar=daily
    Persistent=true

    [Install]
    WantedBy=timers.target

    # 启用:
    # systemctl daemon-reload
    # systemctl enable --now backup.timer

============================================================
输出布局示例
============================================================

源目录: /opt/wechat-selkies
目标目录: /www/backup

/www/backup/
├── path_opt_wechat-selkies_20261007_131831_zpr8xj.tar.gz
│       （第一层普通文件，单卷）
└── wechat-selkies/
    └── path_opt_wechat-selkies_wechat-selkies_20261007_131831_ab12cd.tar.gz.001
        path_opt_wechat-selkies_wechat-selkies_20261007_131831_ab12cd.tar.gz.002
        ...
        （该子目录的递归打包，按 1 GB 分卷）

============================================================
锁文件示例
============================================================

默认锁文件路径（v2.2.3 起）：
    /tmp/<备份目录>_<接收目录>.backup.lock

例如：
    --source /opt/wechat-selkies --dest /www/backup
    => /tmp/opt_wechat-selkies_www_backup.backup.lock

============================================================
退出码
============================================================
0    成功
1    存在失败任务
2    参数/环境检查失败
3    已有另一个备份任务在运行
130  被 Ctrl-C / SIGTERM 中断
============================================================
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
import sys
import tarfile
import time
import traceback
import re
from datetime import datetime


VERSION = "2.2.3"

RANDOM_CHARS = string.ascii_letters + string.digits

DEFAULT_VOLUME_SIZE = 1000 ** 3
DEFAULT_COMPRESSLEVEL = 6
DEFAULT_KEEP = 7
DEFAULT_DISK_SAFETY_FACTOR = 1.10

LOCK_DIR = "/tmp"

ACTIVE_WRITERS = []
INTERRUPTED = False


# ============================================================
# 基础工具
# ============================================================

def human_size(size):
    size = float(size)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
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
    return "".join(secrets.choice(RANDOM_CHARS) for _ in range(length))


def safe_realpath(path):
    try:
        return os.path.realpath(path)
    except Exception:
        return os.path.abspath(path)


def path_is_inside(path, parent):
    path = safe_realpath(path)
    parent = safe_realpath(parent)
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def check_interrupted():
    if INTERRUPTED:
        raise KeyboardInterrupt


def get_file_size(path):
    try:
        st = os.lstat(path)
    except OSError:
        return 0

    if stat.S_ISREG(st.st_mode):
        return st.st_size

    return 0


def estimate_tree_size(path):
    """
    保守估算源数据大小：
    - 普通文件：st_size
    - 符号链接：不跟随，不计目标文件大小
    - 目录：递归
    - 无法访问的项目：返回 (当前估算, 错误数)
    """
    total = 0
    errors = 0

    try:
        st = os.lstat(path)
    except OSError:
        return 0, 1

    if stat.S_ISREG(st.st_mode):
        return st.st_size, 0

    if stat.S_ISLNK(st.st_mode):
        return 0, 0

    if not stat.S_ISDIR(st.st_mode):
        return 0, 0

    stack = [path]

    while stack:
        check_interrupted()
        current = stack.pop()

        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue

                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size

                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)

                    except OSError:
                        errors += 1

        except OSError:
            errors += 1

    return total, errors


def remove_quietly(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def sanitize_path_for_filename(path):
    """
    将绝对路径转换为可用作文件名的字符串。
    - 去掉首尾的 os.sep
    - os.sep 替换为下划线
    - 非 [A-Za-z0-9._-] 字符替换为下划线
    """
    if not path:
        return "root"

    p = path.strip(os.sep)
    p = p.replace(os.sep, "_")
    p = re.sub(r"[^A-Za-z0-9._-]", "_", p)

    if not p:
        p = "root"

    return p


def build_default_lock_path(source, dest):
    """
    默认锁文件路径：
        /tmp/<备份目录>_<接收目录>.backup.lock

    例如：
        source=/opt/wechat-selkies, dest=/www/backup
        => /tmp/opt_wechat-selkies_www_backup.backup.lock

    这样：
    - 不会在目标目录留下锁文件
    - 同一组 source/dest 仍然互斥
    - 不同组 source/dest 使用不同锁，互不干扰
    """
    s = sanitize_path_for_filename(source)
    d = sanitize_path_for_filename(dest)

    return os.path.join(
        LOCK_DIR,
        f"{s}_{d}.backup.lock",
    )


# ============================================================
# 日志
# ============================================================

def log_error(log_path, message, exc=None):
    try:
        parent = os.path.dirname(log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                "[{}] {}\n".format(
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
            f"无法写入日志 {log_path}: {e}",
            file=sys.stderr,
        )


# ============================================================
# Exclude
# ============================================================

class ExcludeMatcher:
    def __init__(self, patterns=None):
        self.patterns = []

        for pattern in patterns or []:
            pattern = pattern.strip()
            if not pattern:
                continue

            self.patterns.append(
                pattern.replace("\\", "/")
            )

    def match(self, relative_path):
        relative_path = relative_path.replace(os.sep, "/")
        basename = os.path.basename(relative_path)

        for pattern in self.patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return True

            if fnmatch.fnmatch(basename, pattern):
                return True

            if "/" not in pattern:
                for part in relative_path.split("/"):
                    if fnmatch.fnmatch(part, pattern):
                        return True

        return False


def load_exclude_file(path):
    patterns = []

    if not path:
        return patterns

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            patterns.append(line)

    return patterns


# ============================================================
# Environment
# ============================================================

class EnvironmentCheck:
    REQUIRED_PYTHON = (3, 9)

    def __init__(
        self,
        source=None,
        dest=None,
        volume_size=DEFAULT_VOLUME_SIZE,
        disk_safety_factor=DEFAULT_DISK_SAFETY_FACTOR,
    ):
        self.source = source
        self.dest = dest
        self.volume_size = volume_size
        self.disk_safety_factor = disk_safety_factor

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
                f"Python 版本过低: {version}，需要 Python "
                f"{self.REQUIRED_PYTHON[0]}."
                f"{self.REQUIRED_PYTHON[1]}+",
                command=self.python_install_command(),
            )

    def python_install_command(self):
        if shutil.which("dnf"):
            return "dnf install -y python3"

        if shutil.which("yum"):
            return "yum install -y python3"

        if shutil.which("apt-get"):
            return "apt-get update && apt-get install -y python3"

        if shutil.which("apk"):
            return "apk add python3"

        return "请安装 Python 3.9 或更高版本"

    def check_linux(self):
        if sys.platform != "linux":
            self.error(
                "当前操作系统不是 Linux。"
                "本生产版针对 Linux/CentOS Stream 9 设计。"
            )

    def check_stdlib(self):
        modules = [
            "argparse",
            "errno",
            "fcntl",
            "fnmatch",
            "gzip",
            "os",
            "platform",
            "secrets",
            "shutil",
            "signal",
            "stat",
            "string",
            "sys",
            "tarfile",
            "time",
            "traceback",
            "re",
        ]

        missing = []

        for module in modules:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)

        if missing:
            self.error(
                "Python 标准库模块缺失: " + ", ".join(missing)
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

        if not os.path.exists(self.source):
            self.error(
                f"源目录不存在: {self.source}",
                command=f"mkdir -p {self.source}",
            )
            return

        if not os.path.isdir(self.source):
            self.error(
                f"源路径不是目录: {self.source}"
            )
            return

        if not os.access(self.source, os.R_OK | os.X_OK):
            self.error(
                f"没有读取源目录的权限: {self.source}",
                command=f"chmod u+rx {self.source}",
            )

    def check_dest(self):
        if not self.dest:
            return

        if not os.path.exists(self.dest):
            parent = os.path.dirname(self.dest.rstrip(os.sep))
            if not parent:
                parent = "/"

            if not os.path.isdir(parent):
                self.error(
                    f"目标目录和父目录都不存在: {self.dest}",
                    command=f"mkdir -p {self.dest}",
                )
                return

            if not os.access(parent, os.W_OK | os.X_OK):
                self.error(
                    f"无法创建目标目录: {self.dest}",
                    command=f"mkdir -p {self.dest}",
                )
                return

            try:
                os.makedirs(self.dest, exist_ok=True)
            except OSError as e:
                self.error(
                    f"无法创建目标目录: {self.dest}: {e}",
                    command=f"mkdir -p {self.dest}",
                )
                return

        if not os.path.isdir(self.dest):
            self.error(
                f"目标路径不是目录: {self.dest}"
            )
            return

        if not os.access(self.dest, os.W_OK | os.X_OK):
            self.error(
                f"没有目标目录写权限: {self.dest}",
                command=f"chmod u+rwx {self.dest}",
            )

    def check_disk_space(self):
        if not self.dest or not self.source:
            return

        try:
            usage = shutil.disk_usage(self.dest)
            free = usage.free

            source_size, errors = estimate_tree_size(self.source)

            if errors:
                self.warning(
                    f"源目录大小估算时有 {errors} 个项目无法读取，"
                    "磁盘检查按已统计数据计算。"
                )

            required = int(
                source_size * self.disk_safety_factor
            )

            if required > free:
                self.error(
                    "目标磁盘空间不足："
                    f"估算源数据 {human_size(source_size)} × "
                    f"{self.disk_safety_factor:.2f} = "
                    f"{human_size(required)}，"
                    f"但目标剩余 {human_size(free)}",
                    command=(
                        f"df -h {self.dest}"
                    ),
                )
            else:
                self.warning(
                    "磁盘空间检查："
                    f"剩余 {human_size(free)}，"
                    f"保守需求约 {human_size(required)}"
                )

        except KeyboardInterrupt:
            raise

        except OSError as e:
            self.warning(
                f"无法获取目标磁盘空间: {e}"
            )

    def check_commands(self):
        if not shutil.which("tar"):
            self.warning(
                "系统未找到 tar。当前 Python backend 不受影响。"
            )

        if not shutil.which("gzip"):
            self.warning(
                "系统未找到 gzip。当前 Python gzip/tarfile backend 不受影响。"
            )

    def check_python_memory(self):
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            pages = os.sysconf("SC_PHYS_PAGES")
            memory = page_size * pages

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
        print(f"服务器环境检查 v{VERSION}")
        print("=" * 72)

        print(
            f"操作系统 : {platform.system()} "
            f"{platform.release()}"
        )

        print(
            f"Python    : {platform.python_version()}"
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
            print("环境检查失败：")

            for item in self.errors:
                print(f"\n[错误] {item['message']}")

                if item.get("command"):
                    print("安装/修复命令:")
                    print("  " + item["command"])

            print()
            print("=" * 72)

            return False

        if self.warnings:
            print()

            for warning in self.warnings:
                print(f"[提示] {warning}")

        print()
        print("[OK] 环境检查通过")
        print("=" * 72)

        return True


# ============================================================
# SplitWriter
# ============================================================

class SplitWriter:
    """
    输出规则：

    base.tar.gz
        单卷

    base.tar.gz.001
    base.tar.gz.002
        多卷

    关键修复：
    1. 只有真正需要写数据时才创建下一卷，避免空分卷。
    2. close() 不会凭空创建最后一个空卷。
    3. 所有卷先写 .tmp，全部成功后再原子改名。
    """

    def __init__(self, base_path, volume_size):
        if volume_size <= 0:
            raise ValueError("volume_size 必须大于 0")

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

        ACTIVE_WRITERS.append(self)

    def _temp_path(self, index):
        return (
            f"{self.base_path}."
            f"{index:03d}.tmp"
        )

    def _final_path(self, index):
        return (
            f"{self.base_path}."
            f"{index:03d}"
        )

    def _open_next(self):
        if self.current is not None:
            self.current.flush()
            self.current.close()
            self.current = None

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
        self.temp_files.append(temp_path)

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
            # 关键：只有有数据需要写时才创建新卷。
            if self.current is None:
                self._open_next()

            remaining = (
                self.volume_size
                - self.current_size
            )

            if remaining <= 0:
                self.current.flush()
                self.current.close()
                self.current = None
                continue

            n = min(len(mv), remaining)
            chunk = mv[:n]

            self.current.write(chunk)

            self.current_size += n
            self.total_size += n
            total_written += n

            mv = mv[n:]

            # 如果刚好写满，不立即创建下一卷。
            # 下一次 write() 有数据时再创建。

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
            os.fsync(self.current.fileno())
        except OSError:
            pass

    def close(self):
        if self.closed:
            return

        if self.aborted:
            raise RuntimeError(
                "无法关闭已经 abort 的 SplitWriter"
            )

        try:
            self._fsync()

            if self.current:
                self.current.close()
                self.current = None

            if not self.temp_files:
                raise RuntimeError(
                    "备份输出为空：没有生成任何数据卷"
                )

            # 防止意外存在空分卷。
            while len(self.temp_files) > 1:
                last = self.temp_files[-1]

                try:
                    size = os.path.getsize(last)
                except OSError:
                    size = 0

                if size != 0:
                    break

                remove_quietly(last)
                self.temp_files.pop()

            if len(self.temp_files) == 1:
                temp_path = self.temp_files[0]
                final_path = self.base_path

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
                    final_path = self._final_path(index)

                    os.replace(
                        temp_path,
                        final_path,
                    )

                    final_files.append(final_path)

                self.final_files = final_files

            self.closed = True

        finally:
            if self in ACTIVE_WRITERS:
                ACTIVE_WRITERS.remove(self)

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

        paths = set(self.temp_files)
        paths.update(self.final_files)

        # base 单卷也属于本次输出。
        paths.add(self.base_path)

        # 清理当前 writer 已经触及的所有可能卷。
        for index in range(
            1,
            self.volume_index + 2,
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
            ACTIVE_WRITERS.remove(self)


# ============================================================
# Backup set / 残留清理
# ============================================================

def find_backup_files(base_path):
    """
    返回：
        {
            "base": 是否存在 base.tar.gz,
            "parts": [(编号, path), ...],
            "temps": [tmp path, ...]
        }

    注意：
    只匹配 base_path 本身以及 base_path.NNN。
    """

    parent = os.path.dirname(base_path) or "."
    filename = os.path.basename(base_path)

    result = {
        "base": os.path.isfile(base_path),
        "parts": [],
        "temps": [],
    }

    try:
        names = os.listdir(parent)
    except OSError:
        return result

    part_re = re.compile(
        r"^"
        + re.escape(filename)
        + r"\.(\d{3,})$"
    )

    tmp_re = re.compile(
        r"^"
        + re.escape(filename)
        + r"\.(\d{3,})\.tmp$"
    )

    base_tmp = filename + ".tmp"

    for name in names:
        full = os.path.join(parent, name)

        if name == base_tmp:
            result["temps"].append(full)
            continue

        m = part_re.match(name)
        if m:
            result["parts"].append(
                (int(m.group(1)), full)
            )
            continue

        m = tmp_re.match(name)
        if m:
            result["temps"].append(full)

    result["parts"].sort(key=lambda x: x[0])
    result["temps"].sort()

    return result


def remove_backup_set(base_path, verbose=True):
    """
    删除一个备份集合：
        base.tar.gz
        base.tar.gz.001...
        base.tar.gz.001.tmp...
        base.tar.gz.tmp
    """

    info = find_backup_files(base_path)

    paths = []

    if info["base"]:
        paths.append(base_path)

    paths.extend(
        path
        for _, path in info["parts"]
    )

    paths.extend(info["temps"])

    removed = 0

    for path in paths:
        try:
            os.remove(path)
            removed += 1

            if verbose:
                print(
                    f"清理损坏/残留备份: {path}"
                )

        except OSError:
            pass

    return removed


# ============================================================
# 文件名
# ============================================================

def generate_base_path(
    directory,
    source_name,
    sub_name,
    timestamp,
):
    """
    检查完整 backup set，而不是只检查 .001。

    防止：
        old.tar.gz
        old.tar.gz.002
    这种不完整集合与新任务冲突。
    """

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

        info = find_backup_files(base_path)

        if not info["base"] and not info["parts"] and not info["temps"]:
            return base_path

    raise RuntimeError(
        "无法生成唯一备份文件名"
    )


# ============================================================
# Tar 打包
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
        # Python 3.9/CentOS Stream 9 兼容：
        # 不把 compresslevel 传给 tarfile.open()。
        # 显式创建 gzip.GzipFile，再把 gzip 流交给 tarfile
        # 的无压缩 streaming writer。这样可以彻底绕开
        # 不同 Python 版本 tarfile -> gzip 参数传递差异。
        gz = gzip.GzipFile(
            fileobj=writer,
            mode="wb",
            compresslevel=compresslevel,
            mtime=0,
        )
        try:
            with tarfile.open(
                fileobj=gz,
                mode="w|",
            ) as tar:

                for path in file_paths:
                    check_interrupted()

                    name = os.path.basename(path)

                    if matcher.match(name):
                        continue

                    # TarFile.add() 内部已经处理了
                    # gettarinfo() 返回 None 的情况
                    # （FIFO / socket 等），这里无需额外处理。
                    tar.add(
                        path,
                        arcname=name,
                        recursive=False,
                    )

        finally:
            gz.close()

        writer.close()

    except BaseException:
        writer.abort()
        raise

    return {
        "output_size": writer.total_size,
        "elapsed": time.monotonic() - start,
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

        # 与 pack_files 相同：显式 gzip 层，兼容 Python 3.9。
        gz = gzip.GzipFile(
            fileobj=writer,
            mode="wb",
            compresslevel=compresslevel,
            mtime=0,
        )
        try:
            with tarfile.open(
                fileobj=gz,
                mode="w|",
            ) as tar:

                # -------------------------------------------------
                # v2.2.2 修复点 1/3：
                # tarfile.gettarinfo() 对 FIFO / Unix socket 等
                # 无法归档的条目会返回 None（而不是抛异常）。
                # 直接 tar.addfile(None) 或 info.isreg() 会崩溃。
                # -------------------------------------------------
                root_info = tar.gettarinfo(
                    dir_path,
                    arcname=arcname,
                )

                if root_info is not None and not matcher.match(arcname):
                    tar.addfile(root_info)

                for root, dirs, files in os.walk(
                    dir_path,
                    topdown=True,
                    followlinks=False,
                ):
                    check_interrupted()

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

                        if matcher.match(tar_name):
                            continue

                        filtered_dirs.append(dirname)

                    dirs[:] = filtered_dirs

                    for dirname in dirs:
                        check_interrupted()

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

                        # v2.2.2 修复点 2/3：
                        # FIFO / socket / 无法归档的目录条目 -> 跳过
                        if info is None:
                            continue

                        if not matcher.match(tar_name):
                            tar.addfile(info)

                    for filename in files:
                        check_interrupted()

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

                        if matcher.match(tar_name):
                            continue

                        info = tar.gettarinfo(
                            full_path,
                            arcname=tar_name,
                        )

                        # v2.2.2 修复点 3/3：
                        # FIFO / socket / 无法归档的文件条目 -> 跳过
                        if info is None:
                            continue

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

        finally:
            gz.close()

        writer.close()

    except BaseException:
        writer.abort()
        raise

    return {
        "output_size": writer.total_size,
        "elapsed": time.monotonic() - start,
        "files": writer.final_files,
    }


# ============================================================
# Verify
# ============================================================

def get_backup_parts(base_path):
    info = find_backup_files(base_path)

    if info["base"] and info["parts"]:
        raise RuntimeError(
            "备份格式冲突：同时存在单卷 .tar.gz "
            "和分卷 .tar.gz.001..."
        )

    if info["base"]:
        return [base_path], "single"

    if not info["parts"]:
        return [], "missing"

    numbers = [n for n, _ in info["parts"]]

    expected = list(
        range(1, len(numbers) + 1)
    )

    if numbers != expected:
        raise RuntimeError(
            "分卷连续性检查失败："
            f"实际编号 {numbers}，"
            f"期望 1..{len(numbers)}"
        )

    return [
        path for _, path in info["parts"]
    ], "split"


class LimitedReader:
    """
    只提供顺序 read()。
    不支持 seek，强制验证逻辑保持真正流式。
    """

    def __init__(self, paths):
        self.paths = paths
        self.index = 0
        self.current = None

    def _open_next(self):
        if self.current:
            self.current.close()

        if self.index >= len(self.paths):
            return False

        self.current = open(
            self.paths[self.index],
            "rb",
            buffering=1024 * 1024,
        )

        self.index += 1
        return True

    def read(self, size=-1):
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
                data = self.current.read(remaining)

            if data:
                chunks.append(data)

                if size >= 0:
                    remaining -= len(data)

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


def verify_backup(base_path):
    parts, backup_type = get_backup_parts(base_path)

    if not parts:
        raise RuntimeError(
            f"找不到备份文件: {base_path}"
        )

    info = find_backup_files(base_path)

    if info["temps"]:
        raise RuntimeError(
            "发现未完成的 .tmp 分卷，"
            "备份集合可能未完整提交"
        )

    print(
        f"验证备份: "
        f"{os.path.basename(base_path)}"
    )

    if backup_type == "single":
        print("    类型: 单卷")
    else:
        print(
            f"    类型: 分卷 "
            f"({len(parts)} 卷)"
        )

    reader = LimitedReader(parts)

    count = 0
    file_count = 0
    content_bytes = 0

    try:
        # 关键修复：
        # 不再使用 gzip.GzipFile + tarfile(r|) 双层包装。
        #
        # tarfile 自己的 r|gz streaming backend 会维护
        # gzip 解压状态，并避免额外的 seek。
        with tarfile.open(
            fileobj=reader,
            mode="r|gz",
        ) as tar:

            while True:
                check_interrupted()

                member = tar.next()

                if member is None:
                    break

                count += 1

                if member.isfile():
                    file_count += 1
                    content_bytes += member.size

        print(
            f"    验证成功: {count} 个 tar 项"
        )

        print(
            f"    文件内容: "
            f"{human_size(content_bytes)} "
            f"({file_count} 个普通文件)"
        )

        return True

    except KeyboardInterrupt:
        raise

    except Exception as e:
        raise RuntimeError(
            f"备份验证失败: {e}"
        ) from e

    finally:
        reader.close()


# ============================================================
# Cleanup old backups
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

    if not os.path.isdir(directory):
        return

    groups = {}

    try:
        names = os.listdir(directory)
    except OSError:
        return

    for name in names:
        if not name.startswith(prefix):
            continue

        match = BACKUP_RE.match(name)

        if not match:
            continue

        base = match.group(1)

        match2 = TIMESTAMP_RE.search(base)

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
            os.path.join(directory, name)
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

    for old_base, info in sorted_groups[keep:]:
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
# Scan
# ============================================================

def scan_source(
    source,
    dest,
    log_path,
):
    file_paths = []
    subdirs = []

    dest_real = safe_realpath(dest)
    log_real = safe_realpath(log_path)

    for entry in os.scandir(source):
        check_interrupted()

        entry_path = os.path.abspath(entry.path)

        if safe_realpath(entry_path) == log_real:
            continue

        if safe_realpath(entry_path) == dest_real:
            continue

        try:
            if entry.is_dir(
                follow_symlinks=False
            ):
                entry_real = safe_realpath(
                    entry.path
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

                subdirs.append(entry.path)

            elif entry.is_file(
                follow_symlinks=False
            ):
                file_paths.append(entry.path)

            elif entry.is_symlink():
                file_paths.append(entry.path)

        except OSError as e:
            print(
                f"扫描失败: {entry.path}: {e}",
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
    print("DRY-RUN：不会创建任何备份")
    print("=" * 72)

    print(f"源目录   : {source}")
    print(f"目标目录 : {dest}")
    print()

    print(
        f"第一层文件: {len(file_paths)} 个"
    )

    for path in file_paths:
        name = os.path.basename(path)

        if matcher.match(name):
            print(f"  [排除] {name}")
        else:
            print(f"  [文件] {name}")

    print()

    print(
        f"一级子目录: {len(subdirs)} 个"
    )

    for path in subdirs:
        name = os.path.basename(
            path.rstrip(os.sep)
        )

        if matcher.match(name):
            print(f"  [排除] {name}")
        else:
            print(f"  [目录] {name}")

    print("=" * 72)


# ============================================================
# Disk check before each task
# ============================================================

def ensure_disk_space(
    dest,
    source_path,
    safety_factor,
):
    try:
        free = shutil.disk_usage(dest).free

        source_size, errors = estimate_tree_size(
            source_path
        )

        required = int(
            source_size * safety_factor
        )

        if required > free:
            raise RuntimeError(
                "磁盘空间不足："
                f"当前剩余 {human_size(free)}，"
                f"本任务源数据估算 "
                f"{human_size(source_size)} × "
                f"{safety_factor:.2f} = "
                f"{human_size(required)}"
            )

        if errors:
            print(
                f"    磁盘检查提示："
                f"{errors} 个项目无法统计大小"
            )

        print(
            f"    磁盘检查："
            f"剩余 {human_size(free)} / "
            f"保守需求 {human_size(required)}"
        )

    except KeyboardInterrupt:
        raise

    except OSError as e:
        raise RuntimeError(
            f"无法检查目标磁盘空间: {e}"
        )


# ============================================================
# Lock
# ============================================================

class FileLock:
    def __init__(self, path):
        self.path = path
        self.fp = None

    def acquire(self):
        parent = os.path.dirname(self.path)

        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                print(
                    f"无法创建锁文件目录 {parent}: {e}",
                    file=sys.stderr,
                )
                return False

        try:
            self.fp = open(self.path, "w")
        except OSError as e:
            print(
                f"无法打开锁文件 {self.path}: {e}",
                file=sys.stderr,
            )
            return False

        try:
            fcntl.flock(
                self.fp.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

        except BlockingIOError:
            self.fp.close()
            self.fp = None
            return False

        self.fp.write(str(os.getpid()))
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

def signal_handler(signum, frame):
    global INTERRUPTED

    INTERRUPTED = True

    signal_name = signal.Signals(signum).name

    print(
        f"\n收到 {signal_name}，"
        "正在安全终止当前备份...",
        file=sys.stderr,
    )

    # 不在 signal handler 内直接关闭 writer。
    #
    # 原来的做法会在 tarfile.write()/文件 IO 中进行
    # 重入式 abort，容易出现：
    #   - 文件句柄正在使用时被关闭
    #   - tarfile 状态损坏
    #   - .tmp / .001 残留
    #
    # 这里只设置全局标记并抛出 KeyboardInterrupt。
    # 外层 finally 统一 abort 所有 ACTIVE_WRITERS。
    raise KeyboardInterrupt


# ============================================================
# nice
# ============================================================

def apply_nice(nice_value):
    if nice_value is None:
        return

    if not hasattr(os, "nice"):
        return

    try:
        os.nice(nice_value)
    except OSError as e:
        print(
            f"设置 nice 失败: {e}",
            file=sys.stderr,
        )


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
        help="gzip 压缩级别 1-9，默认 6",
    )

    parser.add_argument(
        "--disk-safety-factor",
        type=float,
        default=DEFAULT_DISK_SAFETY_FACTOR,
        help=(
            "磁盘空间保守系数，默认 1.10"
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
        help="排除规则文件，每行一个规则",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="备份完成后验证 tar.gz 及全部分卷",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描并显示计划，不实际备份",
    )

    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查服务器环境，不执行备份",
    )

    parser.add_argument(
        "--lock",
        default=None,
        help=(
            "锁文件路径；"
            "默认：/tmp/<备份目录>_<接收目录>.backup.lock"
        ),
    )

    parser.add_argument(
        "--nice",
        type=int,
        default=None,
        help="调整进程 nice 值，例如 10",
    )

    parser.add_argument(
        "--log",
        default=None,
        help="失败日志路径；默认：源目录/backup_error.log",
    )

    return parser


# ============================================================
# Main
# ============================================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    source = os.path.abspath(args.source)
    dest = os.path.abspath(args.dest)

    # --------------------------------------------
    # 参数基础检查
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

    if args.disk_safety_factor <= 0:
        print(
            "--disk-safety-factor 必须大于 0",
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
                f"读取 exclude 文件失败: {e}",
                file=sys.stderr,
            )
            return 2

    matcher = ExcludeMatcher(args.exclude)

    # --------------------------------------------
    # Environment
    # --------------------------------------------

    env = EnvironmentCheck(
        source=source,
        dest=dest,
        volume_size=args.volume_size,
        disk_safety_factor=args.disk_safety_factor,
    )

    try:
        if not env.run():
            return 2
    except KeyboardInterrupt:
        print(
            "\n环境检查已中断。",
            file=sys.stderr,
        )
        return 130

    if args.check_only:
        print()
        print(
            "环境检查完成，未执行备份。"
        )
        return 0

    os.makedirs(dest, exist_ok=True)

    log_path = (
        os.path.abspath(args.log)
        if args.log
        else os.path.join(
            source,
            "backup_error.log",
        )
    )

    # --------------------------------------------
    # v2.2.3：默认锁文件移到 /tmp
    #
    # 命名：<备份目录>_<接收目录>.backup.lock
    # 例如：/tmp/opt_wechat-selkies_www_backup.backup.lock
    #
    # 显式 --lock 时仍然优先生效。
    # --------------------------------------------
    lock_path = (
        os.path.abspath(args.lock)
        if args.lock
        else build_default_lock_path(
            source,
            dest,
        )
    )

    lock = FileLock(lock_path)

    if not lock.acquire():
        print(
            "已有另一个备份任务正在运行，退出。",
            file=sys.stderr,
        )
        print(
            f"（锁文件: {lock_path}）",
            file=sys.stderr,
        )
        return 3

    signal.signal(
        signal.SIGINT,
        signal_handler,
    )

    signal.signal(
        signal.SIGTERM,
        signal_handler,
    )

    apply_nice(args.nice)

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

    overall_start = time.monotonic()

    print()
    print("=" * 72)
    print(f" 自动备份 v{VERSION}")
    print("=" * 72)

    print(f"源目录       : {source}")
    print(f"目标目录     : {dest}")
    print(f"保留备份     : {args.keep}")

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

    print(
        f"磁盘安全系数 : "
        f"{args.disk_safety_factor:.2f}x"
    )

    print(
        f"锁文件       : {lock_path}"
    )

    if matcher.patterns:
        print(
            "排除规则     : "
            + ", ".join(matcher.patterns)
        )

    print("=" * 72)

    try:
        # --------------------------------------------
        # Scan
        # --------------------------------------------

        try:
            file_paths, subdirs = scan_source(
                source,
                dest,
                log_path,
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log_error(
                log_path,
                f"扫描源目录失败: {e}",
                e,
            )
            return 1

        print(
            f"第一层文件   : {len(file_paths)} 个"
        )

        print(
            f"一级子目录   : {len(subdirs)} 个"
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
            base_path = None

            try:
                print()
                print("[1] 打包第一层文件")

                print(
                    f"    文件数 : "
                    f"{len(filtered_files)}"
                )

                ensure_disk_space(
                    dest,
                    source,
                    args.disk_safety_factor,
                )

                base_path = generate_base_path(
                    dest,
                    source_name,
                    None,
                    timestamp,
                )

                print(
                    f"    输出   : {base_path}"
                )

                result = pack_files(
                    filtered_files,
                    base_path,
                    args.volume_size,
                    args.compresslevel,
                    matcher,
                )

                elapsed = result["elapsed"]
                output_size = result["output_size"]

                total_output_size += output_size

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
                    verify_backup(base_path)

                cleanup_old_backups(
                    dest,
                    prefix,
                    args.keep,
                )

                success_count += 1

            except KeyboardInterrupt:
                if base_path:
                    remove_backup_set(
                        base_path,
                        verbose=True,
                    )
                raise

            except Exception as e:
                fail_count += 1
                errors.append(e)

                if base_path:
                    remove_backup_set(
                        base_path,
                        verbose=True,
                    )

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
            check_interrupted()

            sub_name = os.path.basename(
                subdir.rstrip(os.sep)
            )

            print()
            print(
                f"[{index}/{total_tasks}] "
                f"打包目录: {sub_name}"
            )

            if matcher.match(sub_name):
                print(
                    "    [SKIP] 被排除规则排除"
                )
                skipped_count += 1
                continue

            out_dir = os.path.join(
                dest,
                sub_name,
            )

            base_path = None

            try:
                os.makedirs(
                    out_dir,
                    exist_ok=True,
                )

                print(
                    f"    源目录 : {subdir}"
                )

                ensure_disk_space(
                    dest,
                    subdir,
                    args.disk_safety_factor,
                )

                base_path = generate_base_path(
                    out_dir,
                    source_name,
                    sub_name,
                    timestamp,
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

                elapsed = result["elapsed"]
                output_size = result["output_size"]

                total_output_size += output_size

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
                    verify_backup(base_path)

                cleanup_old_backups(
                    out_dir,
                    prefix,
                    args.keep,
                )

                success_count += 1

            except KeyboardInterrupt:
                if base_path:
                    remove_backup_set(
                        base_path,
                        verbose=True,
                    )
                raise

            except Exception as e:
                fail_count += 1
                errors.append(e)

                if base_path:
                    remove_backup_set(
                        base_path,
                        verbose=True,
                    )

                log_error(
                    log_path,
                    f"文件夹打包失败 {subdir}: {e}",
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
            f"成功         : {success_count}"
        )

        print(
            f"失败         : {fail_count}"
        )

        print(
            f"跳过         : {skipped_count}"
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
                f"错误日志     : {log_path}"
            )

        print("=" * 72)

        return 1 if fail_count else 0

    finally:
        # 统一处理中断/异常留下的 writer。
        #
        # SIGTERM 不在 signal handler 里直接 abort，
        # 所以这里是最终清理点。
        for writer in list(ACTIVE_WRITERS):
            try:
                writer.abort()
            except Exception:
                pass

        lock.release()


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:
        for writer in list(ACTIVE_WRITERS):
            try:
                writer.abort()
            except Exception:
                pass

        print(
            "\n备份已中断，临时/未完成备份已清理。",
            file=sys.stderr,
        )

        sys.exit(130)

    except Exception as e:
        for writer in list(ACTIVE_WRITERS):
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
