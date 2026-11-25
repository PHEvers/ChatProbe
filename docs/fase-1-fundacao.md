# Fase 1 do ChatProbe

A URL padrão continua sendo o portal Linx de homologação. O campo **Configurações → Aplicação → URL do chat** aceita HTTP ou HTTPS, com hostname e porta válidos. A aplicação rejeita credenciais embutidas no endereço. Preferências antigas usam o endereço padrão e a mesma configuração atende à execução individual e à fila. O Streamlit lê a preferência salva quando inicia um roteiro.

Groq e Ollama recebem as mesmas instruções de conversa. Uma confirmação curta pode voltar a aparecer se estiver respondendo a outra etapa do chatbot. O runner compara o par formado pela resposta anterior do bot e pela confirmação do cliente em todo o histórico, incluindo retornos a etapas não consecutivas. A comparação ignora caixa, acentos e pontuação. Para mensagens fora das confirmações previstas, o runner mantém a proteção existente contra reenvio. A primeira mensagem de cada teste de um lote guiado continua variando. Essa comparação é textual e variações semânticas sem correspondência textual podem escapar da detecção.

## Relatórios e horários

O contrato do JSON preserva `status`, `message`, `replies` e, nos lotes, `tests`. O relatório individual em formato de lista acrescenta `test_metadata` ao primeiro turno. Quando um teste individual encerra sem turno registrado, os metadados aparecem sob `tests`. Nos lotes cada teste tem metadados próprios, e a raiz inclui o tempo do lote. Durante a fila, `ended_at` permanece nulo no relatório parcial.

| `termination_reason` | Significado |
| --- | --- |
| `completed` | Fim do roteiro ou objetivo indicado como atingido pelo modelo. |
| `loop` | Repetição persistente após nova tentativa ou novo pedido automático de nome. |
| `message_limit` | Limite de mensagens do cliente alcançado. O `status` legado continua `completed`. |
| `interrupted` | Roteiro incompatível com a resposta, abertura repetida, bloqueio informado pelo modelo ou parada solicitada. |
| `technical_error` | Falha na execução, por exemplo na abertura do navegador ou na captura de respostas. |
| `api_limit` | Limite informado pelo provedor, inclusive quando outra sessão do lote é encerrada por esse limite. |

Os campos `started_at`, `ended_at`, `sent_at` e `reply_received_at` usam ISO 8601 com deslocamento UTC. O campo `reply_received_at` tem a mesma ordem de `replies` e indica a primeira observação do texto registrado no navegador. São horários de captura do ChatProbe, com a resolução da sondagem do navegador. A duração é medida por relógio monotônico e registrada em `duration_seconds`. A aplicação converte os horários para o fuso do sistema ao exibir a conversa. A triagem tem evento próprio. Os turnos registrados começam após a triagem.

## SQLite

O banco é criado automaticamente ao rodar um teste pela interface desktop ou pelo Streamlit. A linha do teste começa com estado `running`. Cada mensagem enviada e cada atualização observada nas respostas são gravadas em transações curtas, com conexões próprias para os trabalhadores simultâneos. O encerramento atualiza o teste e registra um evento na mesma transação. Se o processo terminar abruptamente, as gravações confirmadas permanecem disponíveis e o último teste pode continuar marcado como `running`.

O schema usa `PRAGMA user_version`. A versão 1 cria `tests`, `messages` e `events`. A versão 2 adiciona `prompts` como base para a futura biblioteca. As migrações são transacionais e versões mais recentes do banco são preservadas. Em caso de falha de SQLite a interface avisa e mantém os resultados em JSON. As abas de Histórico e Biblioteca de prompts estão previstas para fases futuras.

## Validação no Windows

1. Faça checkout de `feature/phase-1-foundation`. Com Python 3.11 ou superior, rode `python -m unittest discover -s tests -v` na raiz.
2. No PowerShell, execute `.\build-windows.ps1` e abra `dist\ChatProbe\ChatProbe.exe`. O script existente mantém o pacote atual e até dois anteriores.
3. Salve a URL autorizada em **Configurações → Aplicação → URL do chat**. Feche e reabra o aplicativo para conferir a persistência. Tente salvar um endereço inválido e verifique a mensagem de validação.
4. No modo **Roteiro JSON**, execute o exemplo individualmente e depois inclua dois roteiros na fila noturna com simultaneidade 2. Verifique a separação das conversas, os horários locais e os relatórios individuais e do lote.
5. Teste pausa, retomada e parada durante um lote. Confirme que os testes iniciados produzem o resultado correspondente e que a parada impede novos testes.
6. Em **Groq** e **Ollama**, use um chat de teste que solicite duas confirmações idênticas em etapas diferentes e depois volte a uma etapa já respondida. Confira se o modelo repete a confirmação quando pertinente e se o motivo indica loop quando insiste em uma resposta já recusada naquele contexto. Para simular um limite de mensagens, reduza o máximo de mensagens do cliente e confira `message_limit`.
7. Confira `%LOCALAPPDATA%\ChatProbe\chatprobe.sqlite3` após as execuções e confira os horários UTC no JSON. Preserve estes arquivos localmente.
