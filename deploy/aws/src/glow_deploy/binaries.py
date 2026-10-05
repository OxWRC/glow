"""Resolve the terraform/packer executables to invoke.

Resolution order, checked independently for each binary:

1. An explicit override via environment variable (``GLOW_DEPLOY_TERRAFORM_BIN``
   / ``GLOW_DEPLOY_PACKER_BIN``) — mainly for tests and power users.
2. A binary vendored alongside a PyInstaller-frozen build, under
   ``sys._MEIPASS/bin/``.
3. Whatever is on ``PATH`` (the normal case for local/dev/CI use).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from glow_deploy.errors import DeployError


def _frozen_bin_dir() -> Path | None:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "bin"
    return None


def _executable_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _resolve_binary(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override

    frozen_dir = _frozen_bin_dir()
    if frozen_dir is not None:
        candidate = frozen_dir / _executable_name(name)
        if candidate.exists():
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    raise DeployError(
        f"required binary not found: {name} (set {env_var} to point at it explicitly)"
    )


def terraform_binary() -> str:
    """Resolve the terraform executable to invoke."""
    return _resolve_binary("terraform", "GLOW_DEPLOY_TERRAFORM_BIN")


def packer_binary() -> str:
    """Resolve the packer executable to invoke."""
    return _resolve_binary("packer", "GLOW_DEPLOY_PACKER_BIN")
