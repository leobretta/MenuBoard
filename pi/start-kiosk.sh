#!/bin/bash
# Opens tv1 and tv2 full-screen, one per HDMI output (left screen = tv1, right screen = tv2).

BASE_URL="http://localhost:5000"
SWAP=false    # change to true if the two screens are showing the wrong menus

# Wait until the Flask service is answering
until curl -s -o /dev/null "$BASE_URL/tv1"; do sleep 1; done
sleep 2

# Keep the screens from sleeping or blanking
xset s off
xset -dpms
xset s noblank

BROWSER=$(command -v chromium-browser || command -v chromium)

# Geometry of each connected screen (e.g. 1920x1080+0+0), sorted left to right
mapfile -t SCREENS < <(xrandr --query | grep ' connected' \
    | grep -oE '[0-9]+x[0-9]+\+[0-9]+\+[0-9]+' | sort -t+ -k2 -n)

PAGES=(tv1 tv2)
if [ "$SWAP" = true ]; then PAGES=(tv2 tv1); fi

for i in "${!SCREENS[@]}"; do
    [ "$i" -ge "${#PAGES[@]}" ] && break

    geo="${SCREENS[$i]}"
    size="${geo%%+*}"; w="${size%x*}"; h="${size#*x}"
    rest="${geo#*+}";  x="${rest%%+*}"; y="${rest#*+}"

    "$BROWSER" --kiosk --noerrdialogs --disable-infobars \
        --disable-session-crashed-bubble --disable-features=Translate \
        --user-data-dir="/tmp/kiosk-$i" \
        --window-position="$x,$y" --window-size="$w,$h" \
        "$BASE_URL/${PAGES[$i]}" &
done

wait
