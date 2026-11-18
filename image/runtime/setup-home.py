#!/usr/bin/env python3
"""Run as dev after unlocking. Defaults never replace user configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

home = Path.home()
workspace = home / "dev"
subprocess.run(["mountpoint", "-q", str(workspace)], check=True)
os.umask(0o077)
state = workspace / ".agent-container"
def inside(path):
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise RuntimeError("Persistent state would escape the encrypted workspace")
inside(state)
state.mkdir(exist_ok=True)
defaults = Path("/opt/agent-container-home")
def link(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        if destination.readlink() != source:
            raise RuntimeError("Unexpected configuration link: " + str(destination))
    elif destination.exists():
        raise RuntimeError("Refusing to overwrite configuration: " + str(destination))
    else:
        destination.symlink_to(source)

for name in (".zshrc", ".zshenv", ".bashrc", ".p10k.zsh", ".tmux.conf"):
    link(defaults / name, home / name)
for target, directory in [(home/".pi/agent", state/"pi"), (home/".pi/state", state/"pi-state"), (home/".config/nvim", state/"nvim"),
                          (home/".local/share/nvim", state/"nvim-data")]:
    inside(directory)
    directory.mkdir(exist_ok=True)
    link(directory, target)
settings = state / "pi/settings.json"
inside(settings)
if not settings.exists():
    settings.write_text(json.dumps({"enableInstallTelemetry": False, "enableAnalytics": False}, indent=2)+"\n")
init = state / "nvim/init.lua"
if not init.exists():
    init.write_text((defaults/"init.lua").read_text())
config = json.load(sys.stdin)
git = state / "gitconfig"
inside(git)
# Identity is deliberate host configuration; other Git settings belong to user.
for key, value in [("user.name", config["name"]), ("user.email", config["email"]), ("init.defaultBranch", "main")]:
    subprocess.run(["git", "config", "--file", str(git), key, value], check=True)
link(git, home/".gitconfig")
print("User configuration and Pi sessions persist in ~/dev/.agent-container")
