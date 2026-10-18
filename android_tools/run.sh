#!/bin/sh
set -eu
# The isolated UID differs from the CI runner; exported directories must remain
# removable by the runner after artifact validation. No host credentials exist here.
umask 000
# Input is already a sanitized snapshot. No inherited host environment or network.
cp -R /input/. /work/
mkdir -p /work/gradle-home /work/home
cp -R /opt/gradle-cache/. /work/gradle-home/
export HOME=/work/home GRADLE_USER_HOME=/work/gradle-home
/opt/gradle-8.9/bin/gradle --offline --no-daemon --console=plain :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
mkdir -p /output/tests
chmod 0777 /output/tests
cp /work/app/build/outputs/apk/debug/app-debug.apk /output/HorizonOfflineLab-debug.apk
cp /work/app/build/test-results/testDebugUnitTest/TEST-*.xml /output/tests/
cp /work/app/build/reports/lint-results-debug.xml /output/lint.xml
