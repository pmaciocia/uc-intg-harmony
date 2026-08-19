#!/usr/bin/env bash
# Build the aarch64 custom integration archive for the Remote.
set -euo pipefail

PYINSTALLER_IMAGE="docker.io/unfoldedcircle/r2-pyinstaller:3.11.13-0.7.0"
DRIVER_ID=$(python3 -c "import json;print(json.load(open('driver.json'))['driver_id'])")
VERSION=$(python3 -c "import json;print(json.load(open('driver.json'))['version'])")
ARCHIVE="uc-intg-${DRIVER_ID}-${VERSION}-aarch64.tar.gz"

rm -rf build dist artifacts "${ARCHIVE}"

# --platform is a no-op on aarch64 hosts and uses QEMU binfmt emulation on x86-64.
# slixmpp is excluded because the hub connection pins the WEBSOCKETS protocol,
# which never imports the XMPP connector.
docker run --rm --platform=linux/arm64/v8 \
  --user "$(id -u):$(id -g)" \
  -v "${PWD}:/workspace" -w /workspace \
  "${PYINSTALLER_IMAGE}" \
  bash -c "python -m pip install -r requirements.txt && \
    pyinstaller --clean --onedir --name driver \
      --add-data driver.json:. \
      --exclude-module slixmpp \
      --collect-all zeroconf \
      src/driver.py"

mkdir -p artifacts/bin
cp -r dist/driver/. artifacts/bin/
cp driver.json artifacts/
chmod 755 artifacts/bin/driver

tar czf "${ARCHIVE}" -C artifacts .
echo "built ${ARCHIVE} ($(du -h "${ARCHIVE}" | cut -f1))"
