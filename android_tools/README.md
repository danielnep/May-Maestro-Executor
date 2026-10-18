# Android nativo — laboratório sintético e executor

Java/XML nativo, sem WebView, sem dados de cliente, sem permissões de rede. A tela adiciona operações e simula confirmações explicitamente. O núcleo persiste fila em arquivo atômico com fsync, idempotência, concorrência entre threads, backoff e recuperação de operações interrompidas. Não oferece exclusão entre processos independentes, entrega exatamente uma vez remota ou garantia contra perda de energia do dispositivo (diretório não é fsync). Os testes não afirmam essas propriedades.

Cinco cenários densos exercitam transições ilegais/idempotência, 15 retentativas, 8 trabalhadores concorrentes, 1.200 passos aleatórios reproduzíveis com reinícios e falhas reais de arquivo. Testes executam o núcleo Java real, não mocks do resultado. APK, lint e resultados JUnit são produzidos pelo build. Isto prova compilação nativa e lógica; não prova gestos, notificações em segundo plano ou uso em telefone físico.

Preparação de dependências, sem segredos e somente no runner de ferramentas:

```sh
docker build -t horizon-android:agp8.7.3 android_tools
python3 -m android_tools.check --project android_tools/lab --artifacts artifacts/android
```

A imagem prepara o laboratório confiável com rede; o projeto fornecido ao agente **somente** executa depois em container offline, usuário sem privilégios, sem socket Docker ou credenciais. Use a imagem local previamente construída (o executor nunca baixa imagem). Gradle 8.9, AGP 8.7.3, SDK 35, build-tools 34.0.0, JDK 17. Dependências extras de outros aplicativos precisam de imagem confiável explicitamente preparada; erro offline é bloqueio, nunca teste aprovado. Não compilar código de cliente no host com credenciais.

API Python: `isolated_check(project_root, artifacts_dir=None) -> dict` retorna `passed`, `tests_run`, `exit_code`, `cleanup`, `artifacts`, `output`. `passed` exige container bem sucedido, XML válido com testes >0 e zero falhas/erros, lint sem errors e APK ZIP com AndroidManifest.xml/classes.dex. APK é debug, sem chave de publicação.

Referências oficiais: https://developer.android.com/build/releases/agp-8-7-0-release-notes ; https://developer.android.com/tools/sdkmanager ; https://developer.android.com/studio/test/command-line ; https://docs.gradle.org/8.9/userguide/gradle_wrapper.html
