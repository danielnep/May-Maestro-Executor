# Trusted build-tool preparation only; never pass model/provider credentials to build.
FROM eclipse-temurin:17-jdk-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && rm -rf /var/lib/apt/lists/*
ENV ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk GRADLE_USER_HOME=/opt/gradle-cache
RUN curl -fsSL https://services.gradle.org/distributions/gradle-8.9-bin.zip -o /tmp/gradle.zip \
    && curl -fsSL https://services.gradle.org/distributions/gradle-8.9-bin.zip.sha256 -o /tmp/gradle.sha256 \
    && echo "$(cat /tmp/gradle.sha256)  /tmp/gradle.zip" | sha256sum -c - \
    && unzip -q /tmp/gradle.zip -d /opt && rm /tmp/gradle.zip /tmp/gradle.sha256
RUN mkdir -p /opt/android-sdk/cmdline-tools \
    && curl -fsSL https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -o /tmp/android.zip \
    && unzip -q /tmp/android.zip -d /tmp/android-tools \
    && mv /tmp/android-tools/cmdline-tools /opt/android-sdk/cmdline-tools/12.0 \
    && rm -rf /tmp/android.zip /tmp/android-tools
RUN yes | /opt/android-sdk/cmdline-tools/12.0/bin/sdkmanager --licenses >/dev/null \
    ; /opt/android-sdk/cmdline-tools/12.0/bin/sdkmanager 'platforms;android-35' 'build-tools;34.0.0' 'platform-tools'
COPY lab /opt/warmup
WORKDIR /opt/warmup
RUN /opt/gradle-8.9/bin/gradle --no-daemon :app:assembleDebug :app:testDebugUnitTest :app:lintDebug \
    && rm -rf /opt/warmup /opt/gradle-cache/daemon /root/.android
COPY run.sh /opt/horizon-android-run
RUN chmod 755 /opt/horizon-android-run && chmod -R a+rX /opt/android-sdk /opt/gradle-cache
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/bin/sh", "/opt/horizon-android-run"]
