#!/usr/bin/env bash
# Processes a release's images and videos in place, each list holding one path per line:
#
#   media.sh images <list>   resizes the .jpeg, .jpg, .png and .gif files in <list>
#   media.sh videos <list>   re-encodes the .mp4 files in <list>
#
# Everything that decides how media is processed belongs in this file, and nothing else does. The
# media cache (action.yml's "Reuse media processed by an earlier release") reuses a file an earlier
# release processed only when that release ran this same script: its SHA-256 is the cache's key.
# So any change here makes the next release of every protocol process all its media again, and a
# change anywhere else in the action does not. The tools are whatever the runner's apt installs
# (action.yml's "Install tools"); their versions are not part of the key.
set -eo pipefail

# The '>' in --size means only shrink, never enlarge. parallel -X gives each vipsthumbnail a batch of
# files (startup is paid per batch, not per file); each output is written next to its input as
# __resized__<name>, then renamed over it. parallel runs the command through a shell, so it needs
# the inner quotes.
# .jpeg/.jpg files are saved as WebP but keep their names, so references in the protocol JSON still
# work: the app's image decoders (Android 10+, iOS 15+) detect the format from the content.
# WebP Q75 was about 30% smaller than JPEG Q70 and scored higher on SSIM for leia's images.
images() {
  local list=$1
  if [ ! -s "$list" ]; then echo "No images to resize"; return; fi
  time (
    awk '/\.jpeg$/' "$list" | parallel -X vipsthumbnail --size "'650x1300>'" --export-profile srgb -o "'__resized__%s.jpeg.webp[Q=75,strip=true]'"
    awk '/\.jpg$/' "$list" | parallel -X vipsthumbnail --size "'650x1300>'" --export-profile srgb -o "'__resized__%s.jpg.webp[Q=75,strip=true]'"
    awk '/\.png$/' "$list" | parallel -X vipsthumbnail --size "'650x1300>'" --export-profile srgb -o "'__resized__%s.png[profile=none,strip=true,compression=9]'"
    awk '/\.gif$/' "$list" | parallel -X vipsthumbnail --size "'650x1300>'" --export-profile srgb -o "'__resized__%s.gif[strip=true]'"
    while IFS= read -r f; do
      out="${f%/*}/__resized__${f##*/}"
      case "$f" in *.jpeg|*.jpg) out="$out.webp" ;; esac
      mv -f "$out" "$f"
    done < "$list"
  )
}

# Re-encodes each .mp4 to H.264 with its shorter side at most 720 (never enlarged), CRF 26, 8-bit
# 4:2:0 so phones can decode it in hardware, AAC 96k audio, and the index moved to the front so
# playback can start while downloading. A video is only replaced if the result is smaller.
videos() {
  local list=$1
  if [ ! -s "$list" ]; then echo "No videos to compress"; return; fi
  while IFS= read -r f; do
    out="$(dirname "$f")/__resized__$(basename "$f")"
    start=$SECONDS
    ffmpeg -nostdin -hide_banner -loglevel error -y -i "$f" \
      -vf "scale='if(gt(iw,ih),-2,trunc(min(720,iw)/2)*2)':'if(gt(iw,ih),trunc(min(720,ih)/2)*2,-2)'" \
      -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p \
      -c:a aac -b:a 96k -movflags +faststart "$out"
    before=$(stat -c%s "$f")
    after=$(stat -c%s "$out")
    if [ "$after" -lt "$before" ]; then mv "$out" "$f"; result=replaced; else rm "$out"; result="kept original (re-encode was not smaller)"; fi
    echo "$f: $((before / 1024)) KB -> $((after / 1024)) KB in $((SECONDS - start))s, $result"
  done < "$list"
}

case "$1" in
  images) images "$2" ;;
  videos) videos "$2" ;;
  *) echo "usage: media.sh images|videos <list>" >&2; exit 2 ;;
esac
