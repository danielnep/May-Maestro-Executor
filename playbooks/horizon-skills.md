# Horizon Skills | Engenharia

Objetivo: implementar código legível, funcional e testável, observando contratos existentes.

Procedimento:
1. Ler arquitetura, README, instruções, API, banco, permissões e testes existentes.
2. Mapear proprietário do estado e fluxos: entrada → validação → persistência → resposta → interface.
3. Editar a implementação original, remover duplicações e não empilhar override para encobrir defeito.
4. Escrever regressão para o problema observado e rodar testes negativos (duplicidade, acesso, erro e concorrência) quando relevante.
5. Comparar diff, verificar ausência de segredos e conflitos, explicar dependências novas.
6. Usar feature branch. Commit/push só com ferramenta autorizada e verificação posterior do SHA.

Não mexer na main, criar deploy ou reinicializar dados sem autorização expressa. Arquivo escrito não comprova aplicativo executado.

