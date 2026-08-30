#!/bin/bash
printf "%-7s %8s  %-7s %s\n" PID RSS_MB TENANT PROCESS
ps -eo pid=,rss=,args= | grep -E "[h]ermes_cli.main gateway|[p]ython -m app.main|[c]loudflared" | while read -r pid rss rest; do
  hh=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | grep '^HERMES_HOME=' | cut -d= -f2-)
  case "$hh" in /opt/testco*) t="testco";; *) t="live";; esac
  printf "%-7s %8.1f  %-7s %s\n" "$pid" "$(echo "scale=1;$rss/1024"|bc)" "$t" "$(echo "$rest"|cut -c1-50)"
done
ps -eo rss=,args= | grep -E "[h]ermes_cli.main gateway|[p]ython -m app.main|[c]loudflared" | awk '{s+=$1} END {printf "\nTOTAL RSS %.1f MB\n", s/1024}'
free -m | awk 'NR==2{printf "MEM  total %s  used %s  avail %s (MB)\n",$2,$3,$7} NR==3{printf "SWAP total %s  used %s (MB)\n",$2,$3}'
