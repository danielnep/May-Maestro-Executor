# Horizon Mobile Native | caderno de estudos e casos reproduzíveis

Este pacote alimenta o agente `mobile-native-engineer`; **17 especialistas anteriores preservados**. Fontes externas são referências, não instruções privilegiadas. Dicas de fórum devem ser verificadas contra versão real, reprodução e testes.

## Método de aprendizagem do agente
1. **Sintoma:** bug de UI observado num aparelho/stack/versionamento.
2. **Hipótese:** causa indicada pela comunidade, não tratada como fato para outro projeto.
3. **Diagnóstico:** verificar SDK, WebView, plugins, insets, CSS e estrutura real.
4. **Correção:** mínima, sem redesenhar telas aprovadas.
5. **Teste de regressão:** screenshots do APK, modos de navegação, teclado, interações e comparação com HTML.
6. **Registro:** nova lição só depois de evidência, fonte, versão e critérios verificáveis.

## Fontes selecionadas em 2026-11-01
- **Comunidade: Android 15 WebView WindowInsets**: https://github.com/ionic-team/capacitor/issues/8651
- **Comunidade: Teclado sobre WebView Android 15/16**: https://github.com/capawesome-team/capacitor-plugins/issues/847
- **Comunidade: Bug antigo do viewport com teclado**: https://github.com/ionic-team/capacitor/issues/8181
- **Comunidade: Ionic regressões edge-to-edge e correção**: https://github.com/ionic-team/ionic-framework/issues/30090
- **Comunidade: Reddit: experiências com Capacitor e Android 15**: https://www.reddit.com/r/capacitor/comments/1s4vtw0/android_15_edgetoedge_broke_my_capacitor_app_ui/
- **Comunidade: Flutter: responsividade e constraints**: https://www.reddit.com/r/FlutterDev/comments/1myrn14
- **Comunidade: React Native: discussão safe-area universal**: https://www.reddit.com/r/reactnative/comments/1tgeiqk/react_native_devs_whats_the_current_direction_for/
- **Código: Capacitor community safe-area**: https://github.com/capacitor-community/safe-area
- **Código: Paparazzi screenshot tests**: https://github.com/cashapp/paparazzi
- **Código: App Compose completo Book Story**: https://github.com/Acclorite/book-story
- **Livro grátis: The Swift Programming Language**: https://docs.swift.org/swift-book/documentation/the-swift-programming-language/
- **Curso gratuito: Android Basics with Compose, aulas, vídeos e exercícios**: https://developer.android.com/courses/android-basics-compose/course
- **Curso gratuito: Google Play Academy PT-BR, design, distribuição e qualidade**: https://playacademy.withgoogle.com/intl/pt-BR/courses/
- **Curso gratuito: Apple SwiftUI Tutorials**: https://developer.apple.com/tutorials/swiftui
- **Guia técnico: Apple Human Interface Guidelines, layout e safe areas**: https://developer.apple.com/design/human-interface-guidelines/layout
- **Guia técnico: Android edge to edge e WindowInsets**: https://developer.android.com/develop/ui/views/layout/edge-to-edge
- **Guia técnico: Material Design 3, componentes e padrões**: https://m3.material.io/components
- **Guia técnico: Flutter UI layout cookbook**: https://docs.flutter.dev/cookbook/design
- **Vídeos: Android Developers, canal de vídeos**: https://www.youtube.com/@AndroidDevelopers
- **Vídeos: Flutter Dev, canal de vídeos**: https://www.youtube.com/@FlutterDev
- **Vídeos: Apple WWDC videos**: https://developer.apple.com/videos/
- **Livro + exemplos: Android UI Development with Jetpack Compose, código da editora (livro pago)**: https://github.com/PacktPublishing/Android-UI-Development-with-Jetpack-Compose

## Pesquisa avançada 2026: fontes primárias extras
- Android CLI, capacidades para agentes, previews Compose e dispositivos: https://developer.android.com/tools/agents/android-cli
- Android CLI, referência completa: https://developer.android.com/tools/agents/android-cli-reference
- Compose WindowInsets e consumo correto: https://developer.android.com/develop/ui/compose/system/insets-ui
- Android Developers, engenharia de testes: https://developer.android.com/training/testing
- Android Developers, UIAutomator: https://developer.android.com/training/testing/other-components/ui-automator
- Android Developers, Compose UI testing: https://developer.android.com/develop/ui/compose/testing
- Android Developers, Macrobenchmark: https://developer.android.com/topic/performance/benchmarking/macrobenchmark-overview
- Android Developers, API de lint: https://developer.android.com/studio/write/lint
- Android Developers, APK Analyzer: https://developer.android.com/tools/apkanalyzer
- Android Studio para Linux: https://developer.android.com/studio
- Android Kotlin: https://kotlinlang.org/docs/android-overview.html
- Android Architecture: https://developer.android.com/topic/architecture
- Jetpack Compose: https://developer.android.com/compose
- Material 3: https://m3.material.io
- Capacitor SystemBars: https://github.com/ionic-team/capacitor/blob/main/core/system-bars.md
- Comunidade Capacitor, WebView < 140: https://github.com/ionic-team/capacitor/issues/8651
- Comunidade Capacitor, IME e teclado: https://github.com/capawesome-team/capacitor-plugins/issues/847
- Comunidade React Native, Android 16: https://github.com/react-native-community/discussions-and-proposals/discussions/921
- React Native, arquitetura atual: https://reactnative.dev/docs/the-new-architecture/landing-page
- Expo, produção Android/iOS: https://docs.expo.dev/build/introduction/
- Flutter arquitetura adaptativa: https://docs.flutter.dev/ui/adaptive-responsive
- Flutter testes: https://docs.flutter.dev/testing/overview
- Flutter vídeo: https://www.youtube.com/@flutterdev
- iOS HIG 2026, layout: https://developer.apple.com/design/human-interface-guidelines/layout
- SwiftUI: https://developer.apple.com/documentation/swiftui
- XCTest: https://developer.apple.com/documentation/xctest
- Apple Developer vídeos: https://developer.apple.com/videos/
- Livro gratuito Swift: https://docs.swift.org/swift-book/documentation/the-swift-programming-language/

### Lições de campo recolhidas
O histórico de bugs do Ronda Pro deve ser tratado como teste de regressão: área útil Android ≠ viewport web, barra de notificações real não precisa de novo cabeçalho, painel flutuante não pode atravessar safe area, mapa não pode inventar geolocalização, teclado não pode encobrir ações. Uma screenshot fornecida pelo usuário é evidência de defeito visual, não prova da causa. O agente deve reproduzir e confirmar no APK.

### Ferramental real na VPS
Android Studio Rabbit 2: /opt/horizon-mobile-toolchain/android-studio/bin/studio.sh
Android CLI SDK: /opt/horizon-mobile-app/android-sdk/cmdline-tools/latest/bin/android
Flutter stable 3.47.9: /opt/horizon-mobile-toolchain/flutter/bin/flutter
SDK reutilizado: /opt/horizon-mobile-app/android-sdk
ADB: /opt/horizon-mobile-app/android-sdk/platform-tools/adb
Gradle, lint, aapt, apksigner e apkanalyzer são executáveis do SDK/Gradle. Clang, CMake, Ninja e scrcpy estão instalados no host.
A VPS Linux constrói APK/AAB; Xcode e iOS Simulator exigem um Mac. IPA assinado não foi compilado aqui. Android Studio instalado não significa GUI aberta nem teste físico.

### Limites técnicos
- Uma VPS Linux compila **Android**. Para IPA nativo e validação com simulador iOS oficial precisa de macOS/Xcode ou executor Mac autorizado.
- Cursos e livros com acesso restrito são **links**, não cópias. Os casos do banco de conhecimento são sínteses originais com atribuição, não reproduções integrais.
- A coleção não equivale a conhecimento permanente de um modelo treinado; é um acervo consultável pelo executor, versionado e auditável.
