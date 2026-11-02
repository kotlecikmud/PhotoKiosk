"""Network / SMB share mounting and credential management."""
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any


class NetworkShareManager:
    @staticmethod
    def mount_smb_share(
        server: str,
        share_name: str,
        mount_point: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        domain: Optional[str] = None
    ) -> bool:
        """Mounts remote SMB share via mount.cifs safely."""
        if not shutil.which("mount.cifs"):
            return False

        Path(mount_point).mkdir(parents=True, exist_ok=True)
        unc = f"//{server}/{share_name}"

        options = []
        if username:
            options.append(f"username={username}")
        else:
            options.append("guest")

        if password:
            options.append(f"password={password}")

        if domain:
            options.append(f"domain={domain}")

        opt_str = ",".join(options)
        cmd = ["mount", "-t", "cifs", unc, mount_point, "-o", opt_str]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return res.returncode == 0
        except Exception:
            return False

    @staticmethod
    def unmount_share(mount_point: str) -> bool:
        try:
            res = subprocess.run(["umount", mount_point], capture_output=True, check=False)
            return res.returncode == 0
        except Exception:
            return False
