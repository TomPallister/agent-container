export PATH="$HOME/.local/bin:/opt/nvim/bin:$PATH"
export EDITOR=nvim VISUAL=nvim
export XDG_RUNTIME_DIR=/tmp/agent-container-podman
export PI_CODING_AGENT_DIR="$HOME/dev/.agent-container/pi"
export PI_CODING_AGENT_SESSION_DIR="$PI_CODING_AGENT_DIR/sessions"
export PI_TELEMETRY=0 PI_SKIP_VERSION_CHECK=1
export npm_config_nodedir=/usr/local
source /opt/agent-container-home/workflow.sh
[[ ! -f "$HOME/dev/.agent-container/zshenv.local" ]] || source "$HOME/dev/.agent-container/zshenv.local"
