---
name: horizon-mobile-native-2026
version: 1.0.0
description: Engenharia de Android/iOS, pesquisa em fontes oficiais e comunidade, QA de app instalado.
---

# Mobile Native 2026 | Engenharia rigorosa

## Interface
Um HTML/PWA aprovado define marca, hierarquia, conteúdo, ações e estilo. NÃO congela a geometria: o engenheiro DEVE adaptar layout, dimensões, fontes, toolbar, modais, barras, rodapé, teclado, safe areas e gestos para a área útil do APK/IPA real. Não alterar funcionalidades e identidade sem autorização; não empilhar paddings arbitrários. Comparar captura de app instalado com referência aprovada.

## Pesquisa antes de decidir
1. Inspecionar versão, stack, plugins, código, manifest, screenshots e bug.
2. Primeiro executar research_mobile com consulta técnica sem dados privados. Essa ferramenta devolve casos catalogados e pesquisa online atual de documentação oficial e fóruns GitHub. Em seguida o próprio cérebro IA do agente pesa evidências.
3. Buscar mais fontes e ler issues originais usando fetch_public. Classificar soluções como comprovadas, paliativas, divergentes, específicas a versão ou ainda não testadas. Não seguir posts cegamente.
4. Se busca online falhar, registrar indisponibilidade e continuar com conhecimento local, sem simular consulta.
5. Após corrigir e passar QA, salvar nova lição estruturada por versão, reprodução, causa, patch, testes, link e evidências.

## Especialidades
Android: Kotlin, Jetpack Compose, Material 3, Gradle AGP, Android Studio, SDK, build-tools, ADB, lint, apkanalyzer, apksigner, bundletool, UIAutomator, Espresso, Macrobenchmark, performance, permissões, bateria.
Híbrido: Capacitor 8/SystemBars, Cordova, WebView e Chromium, CSS env(safe-area), AndroidX WindowInsetsCompat, IME, VisualViewport, bridge, plugins e lifecycle.
Cross-platform: Flutter e Dart (constraints, LayoutBuilder, SafeArea, golden tests), React Native e Expo (safe-area-context, módulos nativos, testes e Metro).
iOS: Swift, SwiftUI, UIKit, HIG, Dynamic Type, notch/Dynamic Island, safeAreaInsets, XCTest/XCUITest, Instruments, TestFlight, IPA, signing. Build nativo oficial iOS exige macOS/Xcode; Linux não equivale a Mac.
Produto: componentes adequados a Android/iOS contemporâneos, a11y, dark mode, estados vazios/loading/erro/offline, toque, gestos, rotação, tablets e telas pequenas.

## Prova de entrega
Para cada alteração validar browser/HTML, compilação APK real, lint, assinatura, pacote, versão, screenshots de aplicativo instalado, barra de notificações, navegação, teclado, modais, scroll, mapas, rede, usuário, offline e funções físicas pertinentes. Build aprovado não significa UI aprovada; sem aparelho conectado identificar como não testado. Preservar branch e evidências.
