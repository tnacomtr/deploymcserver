#!/usr/bin/env python3
"""Uninstaller for deployserver.py: reverses everything the installer did."""

import curses
import glob
import os
import pathlib
import pwd
import shutil
import subprocess
import sys


# ---------------------------------------------------------------------------
# Output helpers (mirrors deployserver.py style)
# ---------------------------------------------------------------------------

def _critical(msg: str) -> None: print(f"\033[91m[CRITICAL]: {msg}\033[0m")
def _warn(msg: str) -> None:     print(f"\033[93m[WARNING]: {msg}\033[0m")
def _info(msg: str) -> None:     print(f"\033[94m[INFO]: {msg}\033[0m")
def _done(msg: str) -> None:     print(f"\033[92m[DONE]: {msg}\033[0m")
def _task(msg: str) -> None:     print(f"[TASK]: {msg}")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MINECRAFT_USERNAMES = ("minecraft", "mc", "mcserver", "gameserver")

FEDORA_PACKAGES = (
    "tmux", "htop", "iotop-c", "nethogs", "fail2ban",
    "dnf-automatic", "jq",
    "java-1.8.0-openjdk-headless", "java-11-openjdk-headless",
    "java-17-openjdk-headless", "java-21-openjdk-headless",
)

DEBIAN_PACKAGES = (
    "tmux", "htop", "iotop", "nethogs", "fail2ban",
    "unattended-upgrades", "jq",
    "openjdk-8-jre-headless", "openjdk-11-jre-headless",
    "openjdk-17-jre-headless", "openjdk-21-jre-headless",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, *, check: bool = False) -> int:
    result = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check and result.returncode != 0:
        _warn(f"Command exited {result.returncode}: {cmd}")
    return result.returncode


def _detect_distro() -> str:
    """Return 'debian' or 'fedora' based on available package manager."""
    if shutil.which("apt-get"):
        return "debian"
    if shutil.which("dnf"):
        return "fedora"
    raise RuntimeError("Could not detect distro: neither apt-get nor dnf found.")


def _find_minecraft_users() -> list[str]:
    found = []
    for name in MINECRAFT_USERNAMES:
        try:
            pwd.getpwnam(name)
            found.append(name)
        except KeyError:
            pass
    return found


def _find_minecraft_services() -> list[pathlib.Path]:
    return [pathlib.Path(p) for p in glob.glob("/etc/systemd/system/minecraft-*.service")]


# ---------------------------------------------------------------------------
# Removal steps
# ---------------------------------------------------------------------------

def remove_services(services: list[pathlib.Path]) -> None:
    if not services:
        _info("No minecraft systemd services found.")
        return
    for unit in services:
        name = unit.stem
        _task(f"Stopping and disabling {name}...")
        subprocess.run(["systemctl", "stop", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "disable", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unit.unlink(missing_ok=True)
        _done(f"Removed {unit}")
    subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _done("systemd daemon reloaded.")


def remove_users(users: list[str]) -> None:
    if not users:
        _info("No minecraft users found.")
        return
    for user in users:
        _task(f"Removing user '{user}' and home directory...")
        _run(f"userdel -r {user}")
        _done(f"User '{user}' removed.")


def remove_mcrcon() -> None:
    mcrcon = pathlib.Path("/usr/local/bin/mcrcon")
    if mcrcon.exists():
        mcrcon.unlink()
        _done("mcrcon removed.")
    else:
        _info("mcrcon not found, skipping.")


def remove_backup_artifacts() -> None:
    scripts = glob.glob("/usr/local/bin/minecraft-backup-*.sh")
    crons   = glob.glob("/etc/cron.d/minecraft-backup-*")
    for f in scripts + crons:
        pathlib.Path(f).unlink(missing_ok=True)
        _done(f"Removed {f}")
    if not scripts and not crons:
        _info("No backup scripts or cron jobs found.")


def remove_mcrcon_password_file() -> None:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            home = pathlib.Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            home = pathlib.Path("/root")
    else:
        home = pathlib.Path("/root")
    f = home / "mcrcon_password.txt"
    if f.exists():
        f.unlink()
        _done(f"Removed {f}")
    else:
        _info("mcrcon_password.txt not found, skipping.")


def remove_firewall_rules(distro: str) -> None:
    _task("Removing Minecraft firewall rule (port 25565)...")
    if distro == "debian":
        _run("ufw delete allow 25565/tcp")
    else:
        _run("firewall-cmd --permanent --remove-port=25565/tcp")
        _run("firewall-cmd --reload")
    _done("Firewall rule removed.")


def pick_packages(distro: str) -> list[str]:
    pkg_list = list(DEBIAN_PACKAGES if distro == "debian" else FEDORA_PACKAGES)
    selected = [False] * len(pkg_list)

    def _draw(stdscr: "curses.window") -> list[str]:
        curses.curs_set(0)
        current = 0
        while True:
            stdscr.clear()
            stdscr.addstr(0, 0, "Select packages to remove  [↑↓] navigate  [SPACE] toggle  [ENTER] confirm  [q] skip all")
            for i, pkg in enumerate(pkg_list):
                mark = "[x]" if selected[i] else "[ ]"
                attr = curses.A_REVERSE if i == current else curses.A_NORMAL
                stdscr.addstr(i + 2, 2, f"{mark} {pkg}", attr)
            stdscr.refresh()
            key = stdscr.getch()
            if key == curses.KEY_UP:
                current = max(0, current - 1)
            elif key == curses.KEY_DOWN:
                current = min(len(pkg_list) - 1, current + 1)
            elif key == ord(" "):
                selected[current] = not selected[current]
            elif key in (curses.KEY_ENTER, 10, 13):
                break
            elif key == ord("q"):
                return []
        return [pkg for i, pkg in enumerate(pkg_list) if selected[i]]

    return curses.wrapper(_draw)


def remove_packages(distro: str, packages: list[str]) -> None:
    if not packages:
        _info("No packages selected for removal.")
        return
    _task("Removing selected packages...")
    pkgs = " ".join(packages)
    if distro == "debian":
        _run(f"apt-get remove -y --purge {pkgs}")
        _run("apt-get autoremove -y")
    else:
        _run(f"dnf remove -y {pkgs}")
        # Remove EPEL on RHEL derivatives (harmless no-op on pure Fedora)
        _run("dnf remove -y epel-release")
    _done("Packages removed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if os.geteuid() != 0:
        _critical("This script must be run with sudo.")
        sys.exit(1)

    distro = _detect_distro()
    services = _find_minecraft_services()
    users    = _find_minecraft_users()

    print()
    print("The following will be removed:")
    print(f"  Distro detected : {distro}")
    print(f"  Services        : {[s.stem for s in services] or 'none found'}")
    print(f"  Users           : {users or 'none found'}")
    print(f"  mcrcon          : {'/usr/local/bin/mcrcon' if pathlib.Path('/usr/local/bin/mcrcon').exists() else 'not found'}")
    print(f"  Backup artifacts: {glob.glob('/usr/local/bin/minecraft-backup-*.sh') + glob.glob('/etc/cron.d/minecraft-backup-*') or 'none found'}")
    print(f"  Packages        : will be selected interactively")
    print()

    confirm = input("Proceed? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    print()
    remove_services(services)
    remove_users(users)
    remove_mcrcon()
    remove_backup_artifacts()
    remove_mcrcon_password_file()
    remove_firewall_rules(distro)
    packages_to_remove = pick_packages(distro)
    remove_packages(distro, packages_to_remove)

    print()
    _done("Uninstall complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        _warn("Interrupted by user. Aborting.")
        sys.exit(130)
    except subprocess.CalledProcessError as e:
        # A removal command failed; show what ran rather than a raw traceback.
        _critical(f"Command failed (exit {e.returncode}): {e.cmd}")
        sys.exit(1)
    except Exception as e:
        # Surface our own errors, and anything else, as a clean error line
        # instead of crashing with a stack trace.
        _critical(str(e) or e.__class__.__name__)
        sys.exit(1)
