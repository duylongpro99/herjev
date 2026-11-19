# Shared .env helpers for herjev's setup scripts (source this file).

# Value of KEY in a .env file (last one wins), quotes stripped.
env_get() {
  [ -f "$2" ] || return 0
  sed -n "s/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}$1[[:space:]]*=[[:space:]]*//p" "$2" \
    | tail -1 | sed -e "s/^[\"']//" -e "s/[\"'][[:space:]]*$//"
}

# Set KEY=VALUE in a .env file in place, keeping its other lines and its mode
# (600 for a new file).
env_set() {
  python3 - "$1" "$2" "$3" <<'PY'
import os, re, sys
path, key, val = sys.argv[1:]
lines = open(path).read().splitlines() if os.path.exists(path) else []
pat = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
out, done = [], False
for line in lines:
    if pat.match(line):
        if not done:
            out.append(f"{key}={val}")
            done = True
        continue
    out.append(line)
if not done:
    out.append(f"{key}={val}")
mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o600
tmp = path + ".tmp"
with open(tmp, "w") as f:
    f.write("\n".join(out) + "\n")
os.chmod(tmp, mode)
os.replace(tmp, path)
PY
}
