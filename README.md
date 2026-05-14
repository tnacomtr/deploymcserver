# Deploymcserver

> One command. Full Minecraft server. No BS. Under a minute.

A single-file Python CLI that goes from a base Linux install to a running, public-facing Minecraft server: packages, Java, firewall, systemd, everything configured properly, and most importantly, done **securely**.

---

## Quick Start

Download and run:
```bash
curl -O https://raw.githubusercontent.com/tnacomtr/deploymcserver/main/deployserver.py
sudo python3 deployserver.py
```

Or with wget:
```bash
wget https://raw.githubusercontent.com/tnacomtr/deploymcserver/main/deployserver.py
sudo python3 deployserver.py
```

---

## General

**My Personal Motivation**
I've been deploying Minecraft servers for the last 6 years. Setting up and configuring everything took a lot of time. I wanted to massively slash this time since lately I'm switching between VPS providers and I started seeing the impact of manually doing the configuration part myself. So I built a Python script that configures the server to be secure first, then installs and deploys the actual server component in under a minute (benchmarked on fresh Linux installs) and gets it ready to play. There is also the fact that Aternos servers are genuinely unplayable and some people don't bother with self-hosting just because of how long it takes and how hard it seems. With this script it's very effortless. The script is also intentionally modular, so in the future I (or the community via Pull Requests) can add new distros, server types, and fixes over time.

**What does this script do?**
Installs a production-ready Minecraft server on Linux in under a minute. It handles packages, Java, firewall, systemd service, RCON, and optional daily backups automatically.

**What distros are supported?**
Debian, Ubuntu, Linux Mint, and any `debian`-family derivative. Fedora, AlmaLinux, Rocky Linux, RHEL, and any `rhel`/`fedora`-family derivative.

**I want to host a server. Which Linux distro should I pick?**
Go with Debian. It has an entire reputation for being rock-solid. I run all my servers on Debian; they go very strong even when I don't actively log in to maintain them. `unattended-upgrades` (installed by this script) handles all security updates silently in the background, without ever getting in your way.

**Do I need to install anything before running it?**
Just Python 3.10+ and `curl` or `wget`. The script handles everything else.

**What server types are supported?**
Vanilla, Paper, Leaf, and Forge. You can also provide your own direct JAR download URL.

---

## What It Does

1. **Detect your distro** - Fedora/RHEL or Debian/Ubuntu, handled automatically via `/etc/os-release`
2. **Install dependencies** - see package list below
3. **Create a system user** - isolated `minecraft` user under `/opt/`
4. **Pick your server** - choose from Vanilla, Paper, Leaf, or Forge; versions pulled live from official APIs
5. **Install the right Java** - resolved from the Mojang manifest, installed automatically
6. **Deploy everything** - folder structure, JAR download, `eula.txt`, `server.properties`, `start.sh`
7. **Wire up systemd** - service enabled and started on boot
8. **Open the firewall** - SSH (22) and Minecraft (25565) via `firewalld` or `ufw`
9. **Configure RCON** - random password generated, wired into `server.properties`, saved to `~/mcrcon_password.txt`
10. **Optional daily backups** - `tar.gz` archive to `~/minecraft_backups/`, last 3 kept

---

## Packages Installed

**Debian / Ubuntu / Mint:**
`tmux htop iotop-c nethogs fail2ban unattended-upgrades curl wget git rsync jq zip unzip` + Java + mcrcon

**Fedora / AlmaLinux / RHEL:**
`tmux htop iotop-c nethogs fail2ban dnf-automatic curl wget git rsync jq zip unzip` + epel-release (RHEL derivatives only) + Java + mcrcon

---

## Supported Distros

| Family | Tested on |
|---|---|
| Fedora / RHEL | Fedora 44, AlmaLinux 10 |
| Debian / Ubuntu | Debian 12, Linux Mint 22 |

Any other **mutable** derivative of the above (Rocky Linux, Pop!_OS, etc.) should work via `ID_LIKE` detection.

**Can I run this on Windows or macOS?**
No. The script is Linux-only. WSL2 is not supported. If you want to host locally on Windows or macOS, use a VM running a supported Linux distro, or rent a cheap Linux VPS from providers like Hetzner (recommended), DigitalOcean, or Vultr.

**Does this work on immutable distros like Fedora Silverblue or Bazzite?**
No. Immutable distros use fundamentally different package management and filesystem layouts that are incompatible with this script. Use a standard mutable Fedora, Debian, or RHEL-based install instead.

---

## Supported Server Types

| Type | Version source |
|---|---|
| **Vanilla** | Mojang launcher manifest |
| **Paper** | api.papermc.io |
| **Leaf** | api.leafmc.one |
| **Forge** | Maven (minecraftforge.net) |

Version lists are always fetched live. If a version just dropped and isn't in the list yet, type it directly, the script will attempt it.

---

## Requirements

- Linux (mutable, systemd-based)
- Python 3.10+
- Root / sudo
- Internet access (for package installs + JAR download)
- No third-party Python packages. stdlib only

---

## RCON

**What is RCON?**
A protocol that lets you open the server's console to execute commands, such as "/op" or "/give @s dirt".

**How do I connect with RCON?**
```bash
mcrcon -H localhost -P 25575 -p <your-password>
```
The exact command is printed at the end of installation and saved to `~/mcrcon_password.txt`.

**Where is the RCON password stored?**
Printed on screen at the end of installation and saved to `~/mcrcon_password.txt`; the home directory of the user who ran sudo, not the minecraft system user.

---

## Backups

**How do backups work?**
A daily cron job runs at 03:00 and creates a `.tar.gz` archive of the server directory. The last 3 backups are kept, older ones are deleted automatically.

**Where are backups saved?**
`~/minecraft_backups/` - the home directory of the user who ran sudo.

**How do I change the number of backups kept?**
Edit the backup script at `/usr/local/bin/minecraft-backup-<service-name>.sh` and change `MAX_BACKUPS=3`.

---

## No Telemetry. Ever.

This tool makes zero outbound calls except to fetch packages and download the server JAR from official sources. No analytics, no phone-home, no machine IDs.

---
## Uninstallation

Want to remove the server? The repo includes a cleanup script that reverses everything (stops services, deletes the system user, removes cron jobs, and closes the firewall).

```bash
curl -O https://raw.githubusercontent.com/tnacomtr/deploymcserver/main/uninstall.py
sudo python3 uninstall.py
```
OR 

```bash
wget https://raw.githubusercontent.com/tnacomtr/deploymcserver/main/uninstall.py
sudo python3 uninstall.py
```

## Frequently Asked Questions

**Is it safe to run a public Minecraft server with this setup?**
Reasonably so. The server runs as a dedicated unprivileged system user, the firewall only exposes port 25565, fail2ban is installed for SSH protection, and unattended-upgrades/dnf-automatic handles security patches automatically.

**What about Log4Shell (CVE-2021-44228)?**
Minecraft 1.18.1 and later are patched. If you install a version older than 1.18.1 you should add `-Dlog4j2.formatMsgNoLookups=true` to your JVM flags in `start.sh`. This is especially relevant for older Forge installs.

**Why does it need sudo?**
It installs packages, creates a system user, writes systemd unit files, and configures the firewall. all of which require root.

**Can I run it as root directly?**
Yes, but you'll get a warning. Using a regular user with sudo is recommended so backup files and RCON credentials are saved to your home directory rather than `/root`.

**The script says my distro is unsupported. What do I do?**
Check `/etc/os-release` and look at the `ID` and `ID_LIKE` fields. If your distro is a Debian or RHEL derivative that isn't being detected, open an issue on GitHub with those values.

**How does the script know which Java version to install?**
It queries the Mojang version manifest, which specifies the required Java major version for every Minecraft release. For very old versions that predate this field, it falls back to Java 8.

**What if the Java version I need isn't in the repos?**
On Fedora/RHEL the script dynamically queries `dnf` for any available `java-*-openjdk-headless` package that satisfies the required version, so it adapts to whatever is available. On Debian 13+, Java 8 is no longer in the official APT repos. you'll need to install it manually for very old server versions.

**Where is the server installed?**
`/opt/<minecraft-user>/servers/<ServerType>-<version>/`

**Can I install multiple servers?**
Yes. Run the script again. it detects if a folder already exists and appends `-1`, `-2` etc. automatically.

**How do I start/stop the server?**
```bash
sudo systemctl start minecraft-<type>-<version>
sudo systemctl stop minecraft-<type>-<version>
```

**How do I view server logs?**
```bash
sudo journalctl -u minecraft-<type>-<version> -f
```

**What ports does the script open?**
- `22/tcp` - SSH (opened before enabling the firewall so you don't lock yourself out)
- `25565/tcp` - Minecraft

**What firewall backend does it use?**
`ufw` on Debian/Ubuntu/Mint. `firewalld` on Fedora/AlmaLinux/RHEL.

**The WAN IP shown at the end is wrong.**
The script fetches your public IP from `api.ipify.org`. If the result is wrong, your VPS provider may be behind a NAT. Use the IP from your provider's control panel instead.

---

## Issues & Contributing

Found a bug or something not working on your distro? Open an issue on GitHub with:
- Your distro and version (`cat /etc/os-release`)
- The error message or unexpected behavior
- What you were trying to do

PRs are welcome. If you want to add support for a new distro, server type, or feature; go for it. The codebase is intentionally modular so adding a new distro is just a new class, and a new server type is the same.

---

## Special Thanks

Inspired by [mcserver_installer](https://github.com/officialrealTM/mcserver_installer) by [realTM](https://github.com/officialrealTM).
The original shell script and `mcurlgrabber.py` served as the reference and starting point for this rewrite.

---

## License

MIT: see [LICENSE](LICENSE).
