export ZSH=/opt/agent-container-zsh/oh-my-zsh
export ZSH_THEME=powerlevel10k/powerlevel10k
zstyle ':omz:update' mode disabled
plugins=(git zsh-autosuggestions)
source "$ZSH/oh-my-zsh.sh"
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
bindkey -v
HISTFILE="$HOME/dev/.agent-container/zsh_history"
setopt HIST_IGNORE_SPACE
# Space-prefix commands containing credentials; pattern filtering is not complete.
zshaddhistory() {
	[[ "$1" == *"secret""ref:"* || "$1" == *"Authorization:"* ]] && return 1
	return 0
}
autoload -Uz add-zsh-hook
_agent_container_activity() {
	[[ -d /tmp/agent-container ]] && date +%s >| /tmp/agent-container/unlock-ts
	return 0
}
add-zsh-hook preexec _agent_container_activity
add-zsh-hook precmd _agent_container_activity
[[ ! -f "$HOME/dev/.agent-container/zshrc.local" ]] || source "$HOME/dev/.agent-container/zshrc.local"
