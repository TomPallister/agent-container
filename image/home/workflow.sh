# Shared shell helpers. No account or service defaults.
awsrole() {
	if [ "$#" -ne 2 ] || ! printf '%s/%s\n' "$1" "$2" | grep -Eq '^[0-9]{12}/[A-Za-z0-9_+=,.@-]+$'; then
		printf 'usage: awsrole <account-id> <role>\n' >&2
		return 2
	fi
	export AWS_SESSION_TOKEN="secretrole:$1/$2"
}
