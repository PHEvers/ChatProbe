# Fase 3 do ChatProbe

Em **Fila de testes → Controle de execução**, configure intervalos, cotas da execução e novas tentativas. O intervalo entre interações é medido entre envios consecutivos na mesma conversa. Se a resposta e a geração já consumiram esse tempo, o próximo envio pode ocorrer imediatamente. A triagem usa os tempos do próprio navegador. O intervalo entre testes aplica-se à fila. Até o limite de simultaneidade, a primeira leva pode iniciar junta. Cada encerramento adia a ocupação de vagas livres pelo intervalo configurado. Zero desativa a espera correspondente.

**Pausar** suspende novos envios, chamadas e testes em pontos seguros. A chamada HTTP que já iniciou pode terminar no timeout do provedor. **Retomar** libera a fila e volta a consultar as cotas. **Parar** acorda as esperas, impede novas etapas e preserva a conversa já capturada. O navegador é fechado pelo runner quando o teste encerra. Uma operação do navegador em andamento pode precisar concluir ou atingir seu timeout.

## Consumo e recuperação

No gerenciador de provedores, informe quatro números separados por espaços, na ordem **RPM RPD TPM TPD**. RPM e RPD limitam requisições. TPM e TPD limitam tokens. A janela do minuto abrange os últimos 60 segundos e a diária abrange as últimas 24 horas. Zero desativa a respectiva restrição. A cota global usa o endereço do serviço por padrão e independe de chave ou modelo. Use o mesmo **Grupo de cota compartilhada** para endpoints que compartilham plano ou conta. A cota do modelo e a cota específica da execução são aplicadas junto com a global. Se perfis no mesmo grupo configurarem limites diferentes, vale o menor limite ativo.

O SQLite reserva a requisição antes da geração, em uma transação que verifica todas as cotas aplicáveis. A estimativa combina o tamanho em bytes UTF-8 do contexto, uma margem para o protocolo e a reserva para a saída. O consumo medido substitui a estimativa na reserva quando há total confirmado. Sem métrica, a estimativa continua sendo usada para controle e o consumo medido permanece desconhecido. Limites locais são preventivos e o serviço pode usar outra regra de contagem ou renovação. Consumo anterior à Fase 3 não compõe as janelas.

Cada geração e tentativa de recuperação ocupa uma reserva própria. O runner respeita `Retry-After` quando fornecido e usa atraso exponencial com jitter para falhas temporárias sem prazo. Falhas permanentes, como chave inválida ou modelo inexistente, encerram o teste. Um limite diário sinalizado sem prazo não gera novas tentativas na mesma chamada. A espera fica limitada ao valor configurado e o motivo indica cota local ou limite informado pelo provedor. A pausa compartilhada por 429 é persistida. Por padrão, a recuperação automática começa desativada.

Para uma fila grande na Groq, confira as cotas efetivas da conta antes de configurar limites e comece com poucos testes simultâneos. Para Ollama local, deixe as cotas externas em zero quando não se aplicarem e use intervalo e simultaneidade compatíveis com a capacidade da máquina.

## Agendamento e filas salvas

**Agendar…** salva prompts, ordem, provedor, modelo e parâmetros antes da execução. Informe data futura no horário do sistema ou use um deslocamento explícito, por exemplo `2026-12-01 21:00-03:00`. A execução automática requer a aplicação aberta e o computador ligado. Uma fila vencida enquanto a aplicação estava fechada fica disponível para início manual. Se outra fila estiver rodando no horário agendado, o disparo aguarda a vaga. **Recuperar / cancelar…** permite cancelar um agendamento ou iniciar os itens pendentes.

Os itens concluídos permanecem no histórico da mesma fila. Em caso de queda, um item com teste já iniciado é marcado como interrompido após expirar a última atualização da aplicação. As mensagens registradas são recuperadas no SQLite e o item não é reproduzido. Os itens ainda pendentes podem ser iniciados em ordem com a configuração original. Credenciais ficam no armazenamento já configurado e são resolvidas novamente na recuperação. Se o destino de um perfil mudou, restaure a configuração original antes de recuperar a fila.

A migração SQLite adiciona tabelas para filas, itens, reservas de consumo, escopos e pausas de API. Os relatórios JSON preservam os campos anteriores e acrescentam identificador da fila, chamadas por interação, consumo medido e tempo de espera quando disponível. O painel mostra progresso, duração, estimativa aproximada quando há resultados, requisições e tokens conhecidos no minuto e no dia.

## Validação no Windows

1. Execute `python -m unittest discover -s tests -v`, depois `.\build-windows.ps1`. Abra `dist\ChatProbe\ChatProbe.exe`.
2. No modo Roteiro JSON, coloque dois roteiros autorizados na fila. Use simultaneidade 1 e um intervalo de alguns segundos. Confira a regressiva, pause, retome e interrompa uma espera.
3. Em **Recuperar / cancelar…**, retome os itens pendentes e confira a ordem e os resultados preservados. Agende outra fila para alguns minutos à frente e teste o cancelamento.
4. Configure um perfil de teste com limites baixos e recuperação ativa. Examine os indicadores, a causa da espera, as chamadas reportadas no JSON e as reservas no SQLite. Valide cotas remotas apenas com credenciais de teste e os limites permitidos pelo serviço.
