# Integridade transacional — experiência verificada

Fonte: exercício sintético reservations-v1, execução GitHub 39167653151,
Backend Engineer com DeepSeek Flash. Reparou apenas engine.py; passou 3 exemplos
públicos e 5 testes privados em duas sementes (5.376 transições por semente,
competição de processos e encerramento abrupto). A referência não entrou em seu
contexto. Isso comprova o exercício, não certifica todos os projetos ou agentes.

Métodos a considerar, conforme a tecnologia e o contrato da tarefa:

- Escrever invariantes antes de alterar código: conservação de estoque,
  isolamento de locatários, transições terminais e unicidade de eventos.
- A identidade de uma requisição idempotente inclui o locatário. Uma repetição
  deve comparar o payload e devolver o estado persistido; payload conflitante
  precisa falhar sem efeitos. Repetição não renova prazo nem debita novamente.
- Mutação de domínio e inclusão de outbox pertencem à mesma transação. Um commit
  intermediário destrói a garantia de recuperação, mesmo que testes comuns passem.
- No SQLite, avaliar BEGIN IMMEDIATE para operações que primeiro leem e depois
  escrevem em competição. Em PostgreSQL, usar locks/constraints apropriados;
  não transplantar sintaxe SQLite. Testar contenção de verdade.
- Testar igualdade na fronteira temporal, rollback de lote e falha em cada ponto
  entre mutação, evento e commit. Além de exceções, matar um processo e reabrir
  o banco para verificar recuperação durável.
- Reivindicar outbox atomicamente impede repetição no dequeue local. Isso não
  comprova entrega exatamente uma vez em um consumidor externo.
- Separar exemplos públicos de avaliação independente. Usar outras sementes
  e tarefas para detectar correções decoradas. O relato finish não substitui
  resultado real de teste. Registrar capacidades e verificações pendentes.

Não copiar soluções de referência para tarefas avaliadas. Usar estes métodos
para formular hipóteses e testes; preservar os limites e decisões de cada projeto.
