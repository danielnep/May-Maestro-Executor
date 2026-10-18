# Publicação por tipo de entrega

Autorização vigente: **produto novo concluído e validado pode ser publicado
automaticamente na principal**, para visualização. Atualização de produto existente
segue para branch com relatório; publicar na principal exige aprovação explícita
daquela alteração. O objetivo pedido é a base. O Maestro pode pesquisar e justificar
melhorias pertinentes, sem obrigação de acrescentar funcionalidades.

A função `decide_publication` apenas avalia. Não faz push, merge ou deploy.

| Entrega | Destino | Condição |
| --- | --- | --- |
| Trabalho autorizado | `feat/*`, `fix/*`, `agent/*`, `test/*` | Branch com relatório |
| Produto novo | Principal declarada | Alvo do operador, baseline, conclusão e checks reais atuais |
| Atualização existente | Principal declarada | Aprovação específica do conteúdo e commit base, com checks reais |

## Entradas confiáveis

O operador declara `delivery_type` em configuração exclusiva do controlador,
separada de arquivos editáveis, `action.args`, `summary` e memória de especialistas.
O modelo não pode declarar um produto existente como novo para liberar a principal.

Antes de implementar, o controlador chama
`capture_git_baseline(root, "owner/repository")`. O helper verifica o origin e lê
HEAD, árvore, índice e arquivos não rastreados, sem modificar o Git. Ele retorna
`RepositoryBaseline`, com recibo opaco. A política rejeita dicionários de modelo
como baseline. Persistir `baseline.as_dict()` só em estado exclusivo do controlador;
na retomada usar `restore_controller_baseline(record)`. Esse restaurador **não é
ferramenta de modelo** e não deve receber payloads de tarefa ou arquivos do projeto.

Um repositório sem caminhos existentes aceita o fluxo novo. Em um repositório já
ocupado, exigir `new_project_namespace` do operador: o namespace não pode existir
no baseline e todos os caminhos alterados precisam estar dentro dele. Um diretório
existente continua sendo atualização, mesmo que um agente o chame de novo.

Configuração para um produto novo:

```json
{
  "delivery_type": "new_product",
  "target_repo": "owner/new-product",
  "target_branch": "main",
  "allow_new_product_main": true
}
```

Para produto novo em monorepositório existente, acrescentar
`"new_project_namespace": "products/new-product"`. Isso não libera alterações nos
outros produtos.

Atualização existente aprovada usa `delivery_type: "existing_product_update"`,
`allow_existing_product_main: true`, `approval_id` do registro da autorização,
`approved_workspace_digest` do conteúdo aprovado e `approved_base_sha` do commit
base aprovado, além do mesmo vínculo `target_repo` e `target_branch`. O executor
não cria a aprovação por iniciativa própria.

## Evidências e chamada

O runtime calcula `workspace_fingerprint(files)` sobre o snapshot completo do
produto, incluindo testes, imediatamente antes de executar o check. O registro real
recebe:

```json
{
  "source": "runtime",
  "simulated": false,
  "backend": "railway-sdk",
  "execution_id": "identificador-real-da-execucao",
  "passed": true,
  "exit_code": 0,
  "tests_run": 12,
  "timed_out": false,
  "truncated": false,
  "cleanup_ok": true,
  "workspace_digest": "sha256-do-snapshot-executado"
}
```

`cleanup_ok` é obrigatório para `railway-sdk`. Zero testes, erro, timeout,
truncamento, simulação ou digest divergente impedem a principal. Tarefa
`succeeded` e resumo de agente não substituem evidência. Screenshots/revisão de UX
complementam verificações pertinentes; não devem ser transformados em testes
fictícios. Não é obrigatório convocar todos os 17 especialistas em toda entrega.

Antes de publicar, recalcular o fingerprint final, obter os caminhos alterados
reais e consultar o commit atual da principal remota. Mudança posterior ao check
invalida a evidência. Avanço da principal desde o baseline interrompe a publicação
automática de produto novo.

```python
decide_publication(
    repo=target_repo,
    branch=target_branch,
    default_branch=repository_default_branch,
    operator_policy=trusted_operator_record,
    baseline=captured_baseline,
    checks=runtime_verification_records,
    completed=delivery_criteria_satisfied,
    head_sha=current_remote_default_branch_sha,
    changed_paths=actual_changed_paths,
    workspace_digest=current_workspace_fingerprint,
)
```

`head_sha` é `None` explicitamente somente quando a principal realmente não existe.
A decisão contém `allowed`, `reason`, `delivery_type`, `repo`, `branch`,
`production_deploy_allowed: false` e `force_push_allowed: false`.

## Histórico e produção

Criar commits que preservem os pais existentes e atualizar referências sem force,
conferindo novamente o commit remoto esperado. Para repositório vazio, criar o
primeiro commit e referência. Para existente, avançar o histórico sem apagar
commits ou sobrescrever alterações concorrentes.

Publicação de branch/principal não concede deploy de produção, migração de banco,
alteração de credenciais ou exclusão de recursos. Se um push aciona produção, o
controlador deve avaliar essa ação antes do envio; o nome da branch não concede
permissão. Nenhuma outra proteção de infraestrutura ou ferramentas é revogada.

Os testes da política usam árvores Git locais reais, sem publicação remota.
