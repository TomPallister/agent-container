export PATH="$HOME/.local/bin:/opt/nvim/bin:$PATH"
export EDITOR=nvim VISUAL=nvim
export HISTFILE="$HOME/dev/.agent-container/bash_history"
export HISTCONTROL=ignorespace
export XDG_RUNTIME_DIR=/tmp/agent-container-podman
export PI_CODING_AGENT_DIR="$HOME/dev/.agent-container/pi"
export PI_CODING_AGENT_SESSION_DIR="$PI_CODING_AGENT_DIR/sessions"
PROMPT_COMMAND='date +%s > /tmp/agent-container/unlock-ts'
PS1='[agent-container] \w \$ '
[[ ! -f "$HOME/dev/.agent-container/bashrc.local" ]] || source "$HOME/dev/.agent-container/bashrc.local"
