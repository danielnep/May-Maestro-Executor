#!/bin/sh
set -eu
umask 000
cp -R /input/. /work/
mkdir -p /work/gradle-home /work/home
cp -R /opt/gradle-cache/. /work/gradle-home/
export HOME=/work/home GRADLE_USER_HOME=/work/gradle-home
project=/work
if [ ! -f /work/settings.gradle ] && [ -f /work/android/settings.gradle ]; then project=/work/android; fi
cd "$project"
mkdir -p /output/tests
chmod 0777 /output/tests
if [ "${HORIZON_ANDROID_CHECK:-debug}" = release ]; then
 /opt/gradle-8.9/bin/gradle --offline --no-daemon --console=plain :app:assembleRelease :app:lintRelease
 set -- app/build/outputs/apk/release/*.apk
 [ "$#" -eq 1 ] && [ -f "$1" ]
 /opt/android-sdk/build-tools/35.0.0/aapt2 dump badging "$1" >/output/manifest-badging.txt
 cp "$1" /output/Android-release.apk
 cp app/build/reports/lint-results-release.xml /output/lint.xml
else
 /opt/gradle-8.9/bin/gradle --offline --no-daemon --console=plain :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
 cp app/build/outputs/apk/debug/app-debug.apk /output/HorizonOfflineLab-debug.apk
 cp app/build/test-results/testDebugUnitTest/TEST-*.xml /output/tests/
 cp app/build/reports/lint-results-debug.xml /output/lint.xml
fi
