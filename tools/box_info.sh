#!/bin/sh
# Read-only status dump of an Ownice T3 box. Join the box's Wi-Fi first.
BOX=${BOX:-3.3.3.3}

for cgi in checkver checkchannel checkband getwifimode getapchannel; do
  printf '%-14s ' "$cgi"
  curl -s -m 5 "http://$BOX/cgi-bin/$cgi.cgi?t=$(date +%s)" || printf 'no response'
  echo
done

printf '%-14s ' "cert"
echo | openssl s_client -connect "$BOX:443" -servername www.zbox.link 2>/dev/null \
  | openssl x509 -noout -subject -enddate 2>/dev/null | tr '\n' ' '
echo
