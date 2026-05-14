from abc import ABC, abstractmethod
import json
import os
import pathlib
import pwd
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

# Leaf API and Forge Maven metadata return 403 if the request carries Python's default UA.
_UA = "deploymcserver/1.0 (https://github.com)"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _critical(msg: str) -> None:
    print(f"\033[91m[CRITICAL]: {msg}\033[0m")

def _warn(msg: str) -> None:
    print(f"\033[93m[WARNING]: {msg}\033[0m")

def _info(msg: str) -> None:
    print(f"\033[94m[INFO]: {msg}\033[0m")

def _done(msg: str) -> None:
    print(f"\033[92m[DONE]: {msg}\033[0m")

def _task(msg: str) -> None:
    print(f"[TASK]: {msg}")


def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req)


# ---------------------------------------------------------------------------
# Distro ABC + concrete implementations
# ---------------------------------------------------------------------------

class Distro(ABC):
    """Abstract base for all distro-specific operations.

    Subclasses must implement every abstract method. Each method maps to a
    single OS-level concern so that no code outside this class hierarchy ever
    calls apt, dnf, ufw, or firewall-cmd directly.

    Abstract contract:
        packageman()           -> name of the package manager ("apt", "dnf", …)
        install(pkg)           -> install one package by name
        update()               -> upgrade all installed packages
        firewall_allow(p, pr)  -> open port p/protocol in the firewall
        firewall_deny(p, pr)   -> close port p/protocol
        firewall_enable()      -> enable and start the firewall service
        firewall_status()      -> print and return current firewall state
        is_installed(pkg)      -> True if pkg is installed (local DB, no network)
        java_install(major)    -> install the correct JRE for the given Java major

    Concrete helpers provided on the base class (shared by all distros):
        run(), ensure_packages(), ensure_source_packages(), _install_mcrcon(),
        service_*, add_minecraft_user()
    """

    MINECRAFT_USERNAMES = ("minecraft", "mc", "mcserver", "gameserver")
    PACKAGES_NOT_IN_REPOS = ("mcrcon",)

    SOURCE_INSTALLERS: dict = {}

    def __init_subclass__(cls, **kwargs):
        # Re-register on every subclass so the unbound method reference stays valid
        # after the class hierarchy is fully built.
        super().__init_subclass__(**kwargs)
        Distro.SOURCE_INSTALLERS = {
            "mcrcon": Distro._install_mcrcon,
        }

    # --- abstract interface ---------------------------------------------------

    @abstractmethod
    def packageman(self) -> str: ...

    @abstractmethod
    def install(self, pkg: str) -> None: ...

    @abstractmethod
    def update(self) -> None: ...

    @abstractmethod
    def firewall_allow(self, port: int, protocol: str) -> None: ...

    @abstractmethod
    def firewall_deny(self, port: int, protocol: str) -> None: ...

    @abstractmethod
    def firewall_enable(self) -> None: ...

    @abstractmethod
    def firewall_status(self) -> str: ...

    @abstractmethod
    def is_installed(self, pkg: str) -> bool: ...

    @abstractmethod
    def java_install(self, major_version: int) -> None: ...

    # --- concrete shared helpers ---------------------------------------------

    def run(self, cmd: str) -> None:
        subprocess.run(cmd, shell=True, check=True)

    def _refresh_package_db(self) -> None:
        # Distros that need a separate DB refresh (e.g. apt-get update) override this.
        pass

    def ensure_packages(self) -> None:
        # Install any REQUIRED_PACKAGES entries that are not already present.
        missing = [pkg for pkg in self.REQUIRED_PACKAGES if not self.is_installed(pkg)]
        if not missing:
            _done("All required packages already installed.")
            return
        _task(f"Missing packages: {', '.join(missing)}")
        self._refresh_package_db()
        for pkg in missing:
            _task(f"Installing {pkg}...")
            self.install(pkg)
            _done(f"{pkg} installed.")

    def ensure_source_packages(self) -> None:
        # Build or download packages that are not available in any distro repo.
        for pkg in self.PACKAGES_NOT_IN_REPOS:
            if shutil.which(pkg):
                _done(f"{pkg} already installed.")
                continue
            installer = Distro.SOURCE_INSTALLERS.get(pkg)
            if installer:
                _task(f"Installing {pkg} from source...")
                installer(self)
                _done(f"{pkg} installed.")
            else:
                _warn(f"No installer defined for {pkg}, skipping.")

    def _install_mcrcon(self) -> None:
        """Install mcrcon from the latest GitHub release.

        Queries the GitHub Releases API for Tiiffi/mcrcon to resolve the
        current version tag, then downloads the precompiled static Linux
        x86-64 binary zip. Extracts the binary and installs it to
        /usr/local/bin/mcrcon. Skipped entirely if mcrcon is already on PATH.

        Raises:
            RuntimeError: If the GitHub API request or the binary download fails.
        """
        api_url = "https://api.github.com/repos/Tiiffi/mcrcon/releases/latest"
        try:
            with _urlopen(api_url) as resp:
                release = json.loads(resp.read())
        except Exception as e:
            raise RuntimeError(f"Failed to fetch mcrcon release info: {e}") from e

        tag = release["tag_name"]
        zip_url = (
            f"https://github.com/Tiiffi/mcrcon/releases/download/"
            f"{tag}/mcrcon-{tag.lstrip('v')}-linux-x86-64-static.zip"
        )
        _task(f"Fetching mcrcon {tag}...")

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "mcrcon.zip")
            try:
                urllib.request.urlretrieve(zip_url, zip_path)
            except Exception as e:
                raise RuntimeError(f"Failed to download mcrcon: {e}") from e

            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(tmpdir)

            # Binary lives inside a versioned subdirectory, so search the tree.
            binary = next(pathlib.Path(tmpdir).rglob("mcrcon"), None)
            if binary is None or binary.is_dir():
                raise RuntimeError("mcrcon binary not found in downloaded zip")
            binary.chmod(0o755)
            shutil.copy(binary, "/usr/local/bin/mcrcon")

    # --- service management --------------------------------------------------

    def service_enable(self, name: str) -> None:
        self.run(f"systemctl enable {name}")

    def service_disable(self, name: str) -> None:
        self.run(f"systemctl disable {name}")

    def service_start(self, name: str) -> None:
        self.run(f"systemctl start {name}")

    def service_stop(self, name: str) -> None:
        self.run(f"systemctl stop {name}")

    def service_restart(self, name: str) -> None:
        self.run(f"systemctl restart {name}")

    def service_status(self, name: str) -> str:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True
        )
        return result.stdout.strip()

    def service_is_active(self, name: str) -> bool:
        return self.service_status(name) == "active"

    # --- user management -----------------------------------------------------

    def add_minecraft_user(self) -> str:
        # Try each candidate username in order; pick the first one not taken.
        available = None
        for username in self.MINECRAFT_USERNAMES:
            result = subprocess.run(
                ["id", username], capture_output=True, text=True
            )
            if result.returncode != 0:
                available = username
                break

        if available is None:
            raise RuntimeError(
                f"All candidate usernames taken: {self.MINECRAFT_USERNAMES}"
            )

        _task(f"Creating system user '{available}'...")
        self.run(f"useradd -r -m -d /opt/{available} -s /bin/bash {available}")
        _done(f"User '{available}' created at /opt/{available}")
        return available


class Fedora(Distro):

    def __init__(self, distro_id: str = "fedora") -> None:
        self._distro_id = distro_id

    def _refresh_package_db(self) -> None:
        # On RHEL derivatives (AlmaLinux, Rocky, CentOS Stream …) several
        # packages (htop, nethogs, fail2ban) live in EPEL. Pure Fedora has
        # them in its own repos and has no epel-release package.
        if self._distro_id != "fedora":
            result = subprocess.run(["rpm", "-q", "epel-release"], capture_output=True)
            if result.returncode != 0:
                _task("Enabling EPEL repository...")
                self.run("dnf install -y epel-release")
                _done("EPEL enabled.")

    REQUIRED_PACKAGES = (
        "tmux",
        "htop",
        "iotop-c",
        "nethogs",
        "fail2ban",
        "dnf-automatic",
        "curl",
        "wget",
        "git",
        "rsync",
        "jq",
        "zip",
        "unzip",
    )

    JAVA_PACKAGES = {
        8:  "java-1.8.0-openjdk-headless",
        11: "java-11-openjdk-headless",
        17: "java-17-openjdk-headless",
        21: "java-21-openjdk-headless",
    }

    def packageman(self) -> str:
        return "dnf"

    def install(self, pkg: str) -> None:
        self.run(f"dnf install -y {pkg}")

    def update(self) -> None:
        self.run("dnf upgrade -y")

    def is_installed(self, pkg: str) -> bool:
        result = subprocess.run(
            ["rpm", "-q", pkg], capture_output=True, text=True
        )
        return result.returncode == 0

    def _ensure_firewalld_running(self) -> None:
        if self.service_status("firewalld") != "active":
            self.run("systemctl start firewalld")

    def firewall_allow(self, port: int, protocol: str) -> None:
        self._ensure_firewalld_running()
        self.run(f"firewall-cmd --permanent --add-port={port}/{protocol}")
        self.run("firewall-cmd --reload")
        _info(f"Allow rule added for port {port}/{protocol}")

    def firewall_deny(self, port: int, protocol: str) -> None:
        self._ensure_firewalld_running()
        self.run(f"firewall-cmd --permanent --remove-port={port}/{protocol}")
        self.run("firewall-cmd --reload")
        _info(f"Deny rule added for port {port}/{protocol}")

    def firewall_enable(self) -> None:
        self.run("systemctl enable --now firewalld")
        _info("Firewall enabled.")

    def firewall_status(self) -> str:
        result = subprocess.run(
            ["firewall-cmd", "--list-all"], capture_output=True, text=True
        )
        _info(result.stdout.strip())
        return result.stdout

    def _find_java_pkg(self, major_version: int) -> str:
        """Return best available headless JRE package satisfying >= major_version."""
        pkg = self.JAVA_PACKAGES.get(major_version)
        result = subprocess.run(
            ["dnf", "info", pkg], capture_output=True, text=True
        ) if pkg else None
        if result and result.returncode == 0:
            return pkg
        # Discover available java-*-openjdk-headless packages and pick the lowest >= major_version
        res = subprocess.run(
            "dnf list available 'java-*-openjdk-headless' 2>/dev/null | awk '{print $1}'",
            shell=True, capture_output=True, text=True
        )
        candidates = []
        for line in res.stdout.splitlines():
            name = line.split(".")[0]  # strip arch suffix
            parts = name.split("-")
            # package name is java-<ver>-openjdk-headless or java-latest-openjdk-headless
            if len(parts) >= 2 and parts[1].isdigit():
                ver = int(parts[1])
                if ver >= major_version:
                    candidates.append((ver, name))
        if not candidates:
            raise RuntimeError(
                f"No Java {major_version}+ headless package found in dnf repos"
            )
        candidates.sort()
        return candidates[0][1]

    def java_install(self, major_version: int) -> None:
        pkg = self.JAVA_PACKAGES.get(major_version)
        if pkg and self.is_installed(pkg):
            _done(f"Java {major_version} already installed.")
            return
        pkg = self._find_java_pkg(major_version)
        if self.is_installed(pkg):
            _done(f"Java {major_version} already satisfied by {pkg}.")
            return
        _task(f"Installing Java {major_version}+ ({pkg})...")
        self.install(pkg)
        _done(f"Java {major_version} installed.")


class Debian(Distro):

    REQUIRED_PACKAGES = (
        "tmux",
        "htop",
        "iotop",
        "nethogs",
        "fail2ban",
        "unattended-upgrades",
        "curl",
        "wget",
        "git",
        "rsync",
        "jq",
        "zip",
        "unzip",
    )

    JAVA_PACKAGES = {
        8:  "openjdk-8-jre-headless",
        11: "openjdk-11-jre-headless",
        17: "openjdk-17-jre-headless",
        21: "openjdk-21-jre-headless",
    }

    def packageman(self) -> str:
        return "apt"

    def install(self, pkg: str) -> None:
        self.run(f"apt-get install -y {pkg}")

    def update(self) -> None:
        self.run("apt-get update && apt-get upgrade -y")

    def _refresh_package_db(self) -> None:
        self.run("apt-get update")

    def is_installed(self, pkg: str) -> bool:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", pkg],
            capture_output=True, text=True
        )
        return "install ok installed" in result.stdout

    def firewall_allow(self, port: int, protocol: str) -> None:
        self.run(f"ufw allow {port}/{protocol}")
        _info(f"Allow rule added for port {port}/{protocol}")

    def firewall_deny(self, port: int, protocol: str) -> None:
        self.run(f"ufw deny {port}/{protocol}")
        _info(f"Deny rule added for port {port}/{protocol}")

    def firewall_enable(self) -> None:
        self.run("ufw --force enable")
        _info("Firewall enabled.")

    def firewall_status(self) -> str:
        result = subprocess.run(
            ["ufw", "status", "verbose"], capture_output=True, text=True
        )
        _info(result.stdout.strip())
        return result.stdout

    def java_install(self, major_version: int) -> None:
        pkg = self.JAVA_PACKAGES.get(major_version)
        if pkg is None:
            raise RuntimeError(f"No Java {major_version} package known for Debian")
        if self.is_installed(pkg):
            _done(f"Java {major_version} already installed.")
            return
        _task(f"Installing Java {major_version}...")
        self.install(pkg)
        _done(f"Java {major_version} installed.")


# ---------------------------------------------------------------------------
# DistroDetector
# ---------------------------------------------------------------------------

class DistroDetector:

    @staticmethod
    def _read_os_release() -> dict[str, str]:
        # Parse /etc/os-release into a plain key→value dict.
        info: dict[str, str] = {}
        with open("/etc/os-release") as f:
            for line in f:
                line = line.strip()
                if "=" not in line or line.startswith("#"):
                    continue
                k, v = line.split("=", 1)
                info[k] = v.strip('"')
        return info

    @staticmethod
    def detect() -> "Distro":
        """Detect the running distro and return the matching Distro instance.

        Reads /etc/os-release and checks ID and ID_LIKE to determine the
        distro family. ID_LIKE covers derivatives — Ubuntu sets
        ID_LIKE=debian, Rocky Linux sets ID_LIKE="rhel fedora" — so
        derivatives are matched without needing explicit entries.

        Returns:
            Debian: for Debian, Ubuntu, or any distro with "debian" or
                "ubuntu" in ID_LIKE.
            Fedora: for Fedora, RHEL, or any distro with "fedora" or
                "rhel" in ID_LIKE.

        Raises:
            RuntimeError: If the distro cannot be matched to a supported
                family, including the ID and ID_LIKE values for diagnosis.
        """
        info = DistroDetector._read_os_release()
        families = (info.get("ID_LIKE") or info.get("ID", "")).lower().split()
        distro_id = info.get("ID", "").lower()

        if distro_id in ("debian", "ubuntu") or "debian" in families or "ubuntu" in families:
            return Debian()
        if distro_id == "fedora" or "fedora" in families or "rhel" in families:
            return Fedora(distro_id=distro_id)

        raise RuntimeError(
            f"Unsupported distro: ID={info.get('ID')!r}  ID_LIKE={info.get('ID_LIKE')!r}\n"
            "Only Debian/Ubuntu and Fedora/RHEL families are supported."
        )


# ---------------------------------------------------------------------------
# Java resolution  (ported from reimplement/mcurlgrabber.py)
# ---------------------------------------------------------------------------

def _fetch_version_manifest() -> list[dict]:
    # Fetch the full Mojang version manifest listing all release and snapshot entries.
    url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
    try:
        with _urlopen(url) as resp:
            return json.loads(resp.read())["versions"]
    except Exception as e:
        raise RuntimeError(f"Failed to fetch Mojang version manifest: {e}") from e


def find_version_info(ver: str) -> dict:
    # Return the full version JSON for a specific MC version string.
    for entry in _fetch_version_manifest():
        if entry["type"] in ("release", "snapshot") and entry["id"] == ver:
            try:
                with _urlopen(entry["url"]) as resp:
                    return json.loads(resp.read())
            except Exception as e:
                raise RuntimeError(f"Failed to fetch version info for {ver!r}: {e}") from e
    raise ValueError(f"Minecraft version {ver!r} not found in Mojang manifest")


def get_java_major(ver: str) -> int:
    # Look up the Java major version required by a given MC release.
    # Falls back to 8 for old versions that predate the javaVersion field.
    info = find_version_info(ver)
    return info.get("javaVersion", {}).get("majorVersion", 8)


def get_server_url(ver: str) -> str:
    # Return the direct server JAR download URL for a given MC release.
    info = find_version_info(ver)
    try:
        return info["downloads"]["server"]["url"]
    except KeyError:
        raise ValueError(f"No server download available for Minecraft {ver!r}")


# ---------------------------------------------------------------------------
# ServerType ABC + implementations
# ---------------------------------------------------------------------------

class ServerType(ABC):
    """Abstract base for server distribution implementations.

    Each subclass represents one server flavour (Vanilla, Paper, Leaf,
    Forge). Subclasses must implement get_name(), list_versions(), and
    get_download_url(). Override requires_installer() and return True for
    flavours that deliver an installer JAR rather than a ready-to-run
    server JAR (currently only Forge).
    """

    @abstractmethod
    def get_name(self) -> str: ...

    @abstractmethod
    def list_versions(self) -> list[str]: ...

    @abstractmethod
    def get_download_url(self, version: str) -> str: ...

    def requires_installer(self) -> bool:
        return False


class Vanilla(ServerType):

    def get_name(self) -> str:
        return "Vanilla"

    def list_versions(self) -> list[str]:
        versions = _fetch_version_manifest()
        return [v["id"] for v in versions if v["type"] == "release"]

    def get_download_url(self, version: str) -> str:
        return get_server_url(version)


class Paper(ServerType):

    _API = "https://api.papermc.io/v2/projects/paper"

    def get_name(self) -> str:
        return "Paper"

    def list_versions(self) -> list[str]:
        try:
            with _urlopen(self._API) as resp:
                data = json.loads(resp.read())
            return list(reversed(data["versions"]))
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Paper versions: {e}") from e

    def _latest_build(self, version: str) -> int:
        url = f"{self._API}/versions/{version}"
        try:
            with _urlopen(url) as resp:
                data = json.loads(resp.read())
            return max(data["builds"])
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Paper builds for {version!r}: {e}") from e

    def get_download_url(self, version: str) -> str:
        build = self._latest_build(version)
        jar = f"paper-{version}-{build}.jar"
        return f"{self._API}/versions/{version}/builds/{build}/downloads/{jar}"


class Leaf(ServerType):

    _API = "https://api.leafmc.one/v2/projects/leaf"

    def get_name(self) -> str:
        return "Leaf"

    def list_versions(self) -> list[str]:
        try:
            with _urlopen(self._API) as resp:
                data = json.loads(resp.read())
            return list(reversed(data["versions"]))
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Leaf versions: {e}") from e

    def _latest_build(self, version: str) -> int:
        url = f"{self._API}/versions/{version}/builds"
        try:
            with _urlopen(url) as resp:
                data = json.loads(resp.read())
            return max(b["build"] for b in data["builds"])
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Leaf builds for {version!r}: {e}") from e

    def get_download_url(self, version: str) -> str:
        build = self._latest_build(version)
        jar = f"leaf-{version}-{build}.jar"
        return f"{self._API}/versions/{version}/builds/{build}/downloads/{jar}"


class Forge(ServerType):

    _METADATA_URL = (
        "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
    )

    def get_name(self) -> str:
        return "Forge"

    def _fetch_all_combos(self) -> list[str]:
        # Each entry in the XML is "{mc_version}-{forge_version}".
        try:
            with _urlopen(self._METADATA_URL) as resp:
                root = ET.fromstring(resp.read())
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Forge metadata: {e}") from e
        return [v.text for v in root.findall(".//version") if v.text]

    def list_versions(self) -> list[str]:
        # Deduplicate to MC versions only, newest first.
        combos = self._fetch_all_combos()
        seen: set[str] = set()
        result: list[str] = []
        for combo in reversed(combos):
            mc_ver = combo.split("-")[0]
            if mc_ver not in seen:
                seen.add(mc_ver)
                result.append(mc_ver)
        return result

    def _latest_forge_ver(self, mc_version: str) -> str:
        combos = self._fetch_all_combos()
        candidates = [c for c in combos if c.startswith(f"{mc_version}-")]
        if not candidates:
            raise ValueError(f"No Forge build found for Minecraft {mc_version!r}")
        # Maven XML lists versions in ascending order, so the last entry is the newest.
        return candidates[-1].split("-", 1)[1]

    def get_download_url(self, version: str) -> str:
        forge_ver = self._latest_forge_ver(version)
        base = "https://maven.minecraftforge.net/net/minecraftforge/forge"
        return f"{base}/{version}-{forge_ver}/forge-{version}-{forge_ver}-installer.jar"

    def requires_installer(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Installer functions
# ---------------------------------------------------------------------------

def create_server_folder(username: str, server_type: str, version: str) -> pathlib.Path:
    # Create /opt/<username>/servers/<Type>-<version>/, appending -1, -2 … if it already exists.
    base = pathlib.Path(f"/opt/{username}/servers")
    base.mkdir(parents=True, exist_ok=True)
    candidate = base / f"{server_type}-{version}"
    i = 1
    while candidate.exists():
        candidate = base / f"{server_type}-{version}-{i}"
        i += 1
    candidate.mkdir()
    # The service runs as the minecraft user, so it needs write access to its own directory.
    subprocess.run(["chown", "-R", f"{username}:{username}", str(base)], check=True)
    _done(f"Server folder created: {candidate}")
    return candidate


def download_jar(url: str, dest: pathlib.Path) -> None:
    _task("Downloading JAR...")
    try:
        with _urlopen(url) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        raise RuntimeError(f"Failed to download server JAR: {e}") from e
    _done(f"Downloaded to {dest}")


def run_forge_installer(server_dir: pathlib.Path) -> None:
    # Run the Forge installer JAR in --installServer mode, then clean up the installer.
    installer = next(server_dir.glob("*installer.jar"), None)
    if installer is None:
        raise RuntimeError("Forge installer JAR not found in server directory")
    _task("Running Forge installer (this may take a while)...")
    subprocess.run(
        ["java", "-jar", str(installer), "--installServer"],
        cwd=server_dir,
        check=True,
    )
    installer.unlink()
    # Pre-1.17 Forge produced a shim jar that needed renaming to server.jar.
    # Modern Forge (≥1.17) produces run.sh instead; write_start_sh handles that.
    for candidate in server_dir.glob("forge-*-shim.jar"):
        candidate.rename(server_dir / "server.jar")
        break
    _done("Forge installer complete")


def write_eula(server_dir: pathlib.Path) -> None:
    (server_dir / "eula.txt").write_text("eula=true\n")
    _done("eula.txt written")


def generate_rcon_password() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def write_server_properties(server_dir: pathlib.Path, rcon_password: str) -> None:
    props = (
        "server-port=25565\n"
        "online-mode=true\n"
        "motd=A Minecraft Server\n"
        "max-players=20\n"
        "difficulty=normal\n"
        "enable-rcon=true\n"
        "rcon.port=25575\n"
        f"rcon.password={rcon_password}\n"
    )
    (server_dir / "server.properties").write_text(props)
    _done("server.properties written")


def print_rcon_password(password: str, save_path: pathlib.Path) -> None:
    cmd_line  = f"mcrcon -H localhost -P 25575 -p {password}"
    save_line = f"Also saved to: {save_path}"
    inner_w = max(len("⚠  SAVE YOUR RCON PASSWORD  ⚠"), len(cmd_line), len(save_line)) + 4
    w = inner_w + 4  # 2 border chars each side

    def _border() -> str:
        return "\033[1;93m" + "█" * w + "\033[0m"

    def _empty() -> str:
        return "\033[1;93m██\033[0m" + " " * (w - 4) + "\033[1;93m██\033[0m"

    def _row(text: str, visible_len: int | None = None) -> str:
        vlen = visible_len if visible_len is not None else len(text)
        pad = w - 4 - vlen
        left = pad // 2
        right = pad - left
        return f"\033[1;93m██\033[0m{' ' * left}{text}{' ' * right}\033[1;93m██\033[0m"

    print()
    print(_border())
    print(_empty())
    print(_row("⚠  SAVE YOUR RCON PASSWORD  ⚠"))
    print(_empty())
    # Password in bold white — visible length is just the 8 chars, not the ANSI codes
    print(_row(f"\033[1;97m{password}\033[0m", visible_len=len(password)))
    print(_empty())
    print(_row(cmd_line))
    print(_empty())
    print(_row(save_line))
    print(_empty())
    print(_border())
    print()


def get_server_ips() -> tuple[str, str]:
    try:
        # UDP connect does not send any packets; it just binds the socket so we can
        # read back which local interface the OS would use to reach the internet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "unavailable"
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=5) as resp:
            public_ip = resp.read().decode().strip()
    except Exception:
        public_ip = "unavailable"
    return local_ip, public_ip


def print_success_box(local_ip: str, public_ip: str) -> None:
    donate_raw   = "Consider donating to support development!"
    donate_plain = donate_raw  # same length, used for width calc
    doc_line     = "Docs & source: https://github.com/deploymcserver/deploymcserver"
    local_line   = f"Local network:   {local_ip}:25565"
    public_line  = f"Internet (WAN):  {public_ip}:25565"
    title        = "✔  SERVER IS UP AND READY TO PLAY!  ✔"

    inner_w = max(len(title), len(donate_plain), len(doc_line),
                  len(local_line), len(public_line)) + 4
    w = inner_w + 4

    def _border() -> str:
        return "\033[1;92m" + "█" * w + "\033[0m"

    def _empty() -> str:
        return "\033[1;92m██\033[0m" + " " * (w - 4) + "\033[1;92m██\033[0m"

    def _row(text: str, visible_len: int | None = None) -> str:
        vlen = visible_len if visible_len is not None else len(text)
        pad = w - 4 - vlen
        left = pad // 2
        right = pad - left
        return f"\033[1;92m██\033[0m{' ' * left}{text}{' ' * right}\033[1;92m██\033[0m"

    # "donating" highlighted bold green within the donate line
    donate_styled = "Consider \033[1;92mdonating\033[0m to support development!"

    print()
    print(_border())
    print(_empty())
    print(_row(f"\033[1;97m{title}\033[0m", visible_len=len(title)))
    print(_empty())
    print(_row(local_line))
    print(_row(public_line))
    print(_empty())
    print(_row(donate_styled, visible_len=len(donate_plain)))
    print(_row(doc_line))
    print(_empty())
    print(_border())
    print()


def write_start_sh(server_dir: pathlib.Path, java_major: int, ram: str) -> None:
    if (server_dir / "run.sh").exists():
        # Modern Forge (≥1.17) launches via run.sh; heap args go in user_jvm_args.txt.
        (server_dir / "run.sh").chmod(0o755)
        jvm_args = server_dir / "user_jvm_args.txt"
        existing = jvm_args.read_text() if jvm_args.exists() else ""
        if "-Xmx" not in existing:
            with open(jvm_args, "a") as f:
                f.write(f"-Xmx{ram}\n-Xms512M\n")
        content = "#!/bin/bash\nexec ./run.sh\n"
    else:
        content = (
            "#!/bin/bash\n"
            f"# Java {java_major} required\n"
            f"exec java -Xmx{ram} -Xms512M -jar server.jar nogui\n"
        )
    start_sh = server_dir / "start.sh"
    start_sh.write_text(content)
    start_sh.chmod(0o755)
    _done("start.sh written")


def write_systemd_unit(
    server_dir: pathlib.Path,
    username: str,
    service_name: str,
) -> None:
    unit = (
        "[Unit]\n"
        f"Description=Minecraft Server ({service_name})\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"User={username}\n"
        f"WorkingDirectory={server_dir}\n"
        f"ExecStart={server_dir}/start.sh\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    unit_path = pathlib.Path(f"/etc/systemd/system/{service_name}.service")
    unit_path.write_text(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    _done(f"Systemd unit written: {unit_path}")


# ---------------------------------------------------------------------------
# CLI pickers
# ---------------------------------------------------------------------------

SERVER_TYPES: list[ServerType] = [Vanilla(), Paper(), Leaf(), Forge()]


def pick_server_type() -> ServerType:
    _info("Server types:")
    for i, st in enumerate(SERVER_TYPES, 1):
        print(f"  {i}. {st.get_name()}")
    while True:
        raw = input(f"Select [1-{len(SERVER_TYPES)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(SERVER_TYPES):
            selected = SERVER_TYPES[int(raw) - 1]
            if isinstance(selected, Forge):
                print("\033[91m[WARNING]: Some Forge servers are known to crash on first startup due to bugs in Forge itself. This is not a script issue. Please look up the latest stable version before proceeding.\033[0m")
            return selected
        _warn("Invalid selection, try again.")


def pick_version(server: ServerType) -> str:
    _task(f"Fetching {server.get_name()} versions...")
    try:
        versions = server.list_versions()[:20]
    except RuntimeError as e:
        _warn(f"Could not fetch version list: {e}")
        versions = []

    if versions:
        _info("Available versions (newest first):")
        for i, v in enumerate(versions, 1):
            print(f"  {i:2}. {v}")
        print()

    while True:
        raw = input("Enter a list number or type a version directly: ").strip()
        if raw.isdigit() and versions and 1 <= int(raw) <= len(versions):
            return versions[int(raw) - 1]
        if raw:
            return raw
        _warn("Please enter a version.")


def _ask_java_major() -> int:
    print("Which Java version does this JAR require?")
    print("  1. Java 8   2. Java 17   3. Java 21")
    mapping = {"1": 8, "2": 17, "3": 21}
    while True:
        choice = input("Select [1-3]: ").strip()
        if choice in mapping:
            return mapping[choice]
        _warn("Invalid choice.")


def pick_ram() -> str:
    presets = ["1G", "2G", "3G", "4G", "6G", "8G"]
    print("\nHow much RAM to allocate?")
    for i, p in enumerate(presets, 1):
        print(f"  {i}. {p}")
    print(f"  {len(presets) + 1}. Custom")
    while True:
        raw = input(f"Select [1-{len(presets) + 1}]: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(presets):
                return presets[idx - 1]
            if idx == len(presets) + 1:
                custom = input("Enter amount (e.g. 3G, 768M): ").strip()
                if custom:
                    return custom
        _warn("Invalid selection.")


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def write_backup_script(
    server_dir: pathlib.Path,
    backup_dir: pathlib.Path,
    owner: str,
    script_path: pathlib.Path,
) -> None:
    # Write a self-contained bash script that archives the server dir and trims old backups.
    script = (
        "#!/bin/bash\n"
        f'SERVER_DIR="{server_dir}"\n'
        f'BACKUP_DIR="{backup_dir}"\n'
        "MAX_BACKUPS=3\n"
        "\n"
        'mkdir -p "$BACKUP_DIR"\n'
        'TIMESTAMP=$(date +%Y-%m-%dT%H%M%S)\n'
        'BACKUP_FILE="$BACKUP_DIR/minecraft-backup-$TIMESTAMP.tar.gz"\n'
        "\n"
        'tar -czf "$BACKUP_FILE" -C "$(dirname "$SERVER_DIR")" "$(basename "$SERVER_DIR")"\n'
        "\n"
        'ls -1t "$BACKUP_DIR"/minecraft-backup-*.tar.gz 2>/dev/null'
        ' | tail -n +$((MAX_BACKUPS + 1)) | xargs -r rm --\n'
        "\n"
        f'chown -R {owner}: "$BACKUP_DIR"\n'
    )
    script_path.write_text(script)
    script_path.chmod(0o755)
    _done(f"Backup script written: {script_path}")


def install_backup_cron(script_path: pathlib.Path, cron_name: str) -> None:
    """Install a daily cron job that runs the backup script as root.

    Writes to /etc/cron.d/ so the job is system-wide and survives reboots.
    The job runs as root rather than as the minecraft user because the server
    directory under /opt/<minecraft-user>/ requires elevated read access for
    consistent archiving, and the backup destination is owned by the invoking
    sudo user — not by the minecraft service account.

    Args:
        script_path: Absolute path to the backup shell script to execute.
        cron_name: Filename stem used for the /etc/cron.d/ entry.
    """
    cron_file = pathlib.Path(f"/etc/cron.d/{cron_name}")
    cron_file.write_text(f"0 3 * * * root {script_path}\n")
    _done(f"Daily backup cron installed: {cron_file} (runs at 03:00)")


def prompt_backup(server_dir: pathlib.Path, service_name: str) -> None:
    # Ask the user whether to opt in to daily backups; no-op on decline.
    answer = input("\nSet up daily backups? [y/N]: ").strip().lower()
    if answer != "y":
        return

    sudo_user = os.environ.get("SUDO_USER")
    home = pathlib.Path(pwd.getpwnam(sudo_user).pw_dir) if sudo_user else pathlib.Path("/root")
    backup_dir = home / "minecraft_backups"
    owner = sudo_user or "root"

    script_path = pathlib.Path(f"/usr/local/bin/minecraft-backup-{service_name}.sh")
    write_backup_script(server_dir, backup_dir, owner, script_path)
    install_backup_cron(script_path, f"minecraft-backup-{service_name}")
    _done(f"Backups will be saved to {backup_dir}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def check_execution_context() -> None:
    # Enforce root and warn if running as direct root rather than via sudo.
    if os.geteuid() != 0:
        _critical("This script must be run with sudo.")
        sys.exit(1)

    if os.environ.get("SUDO_USER") is None:
        _warn("You are logged in directly as root. Consider using a regular user account.")


if __name__ == "__main__":
    check_execution_context()

    distro = DistroDetector.detect()

    distro.ensure_packages()
    distro.ensure_source_packages()
    username = distro.add_minecraft_user()

    _info("How do you want to provide the server JAR?")
    print("  1. Pick from a known server type (Vanilla / Paper / Leaf / Forge)")
    print("  2. Provide my own download URL")
    while True:
        _choice = input("Select [1-2]: ").strip()
        if _choice in ("1", "2"):
            break
        _warn("Invalid choice.")

    if _choice == "1":
        server = pick_server_type()
        version = pick_version(server)
        _task("Resolving download URL...")
        jar_url = server.get_download_url(version)
        try:
            java_major = get_java_major(version)
        except Exception:
            java_major = _ask_java_major()
        server_type_name = server.get_name()
        needs_installer = server.requires_installer()
    else:
        jar_url = input("Paste the direct JAR download URL: ").strip()
        java_major = _ask_java_major()
        version = input("Version label for folder name (e.g. 1.21.4): ").strip() or "custom"
        server_type_name = "Server"
        needs_installer = False

    distro.java_install(java_major)

    server_dir = create_server_folder(username, server_type_name, version)
    # Installer JARs must keep their original filename; run_forge_installer globs for *installer.jar.
    jar_filename = jar_url.split("/")[-1] if needs_installer else "server.jar"
    download_jar(jar_url, server_dir / jar_filename)

    if needs_installer:
        run_forge_installer(server_dir)

    rcon_password = generate_rcon_password()
    sudo_user = os.environ.get("SUDO_USER")
    home = pathlib.Path(pwd.getpwnam(sudo_user).pw_dir) if sudo_user else pathlib.Path("/root")
    rcon_save_path = home / "mcrcon_password.txt"
    rcon_save_path.write_text(
        f"RCON password: {rcon_password}\n"
        f"Connect with: mcrcon -H localhost -P 25575 -p {rcon_password}\n"
    )
    write_eula(server_dir)
    write_server_properties(server_dir, rcon_password)

    ram = pick_ram()
    write_start_sh(server_dir, java_major, ram)

    service_name = f"minecraft-{server_type_name.lower()}-{version}".replace(".", "-")
    write_systemd_unit(server_dir, username, service_name)

    distro.service_enable(service_name)
    distro.service_start(service_name)
    distro.firewall_allow(22, "tcp")       # SSH — before enabling firewall or we lock ourselves out
    distro.firewall_allow(25565, "tcp")    # Minecraft
    distro.firewall_enable()

    prompt_backup(server_dir, service_name)

    _done(f"Server installed at {server_dir}")
    _done(f"Service '{service_name}' enabled and started.")
    print_rcon_password(rcon_password, rcon_save_path)
    local_ip, public_ip = get_server_ips()
    print_success_box(local_ip, public_ip)
