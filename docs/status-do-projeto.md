# Andamento do ChatProbe

Atualizado em 24/11/2026.

O ChatProbe executou um roteiro JSON e registrou uma conversa com o AutoSystem em homologação pelo terminal. O aplicativo Windows oferece os modos Groq e Ollama, lotes e uma fila de testes. Os fluxos do aplicativo foram verificados com simulação local.

| Entrega | Situação | Evidência ou trabalho restante |
| --- | --- | --- |
| Abertura do chat e triagem | Validada em homologação | O script encontrou o chat, enviou o CNPJ de teste, selecionou Suporte Técnico e AUTOSYSTEM e aguardou o pedido do motivo do contato. A abertura faz até cinco tentativas de localizar o botão, com quatro recargas. |
| Envio e captura da conversa | Validados em homologação | O teste de cupom registrou dois turnos na mesma sessão. O relatório JSON local contém duas mensagens enviadas e uma resposta do bot para cada uma. |
| Roteiro JSON com respostas condicionais | Validado em homologação | O exemplo de cupom envia o nome de teste quando a resposta contém “seu nome”. A captura aguarda a resposta estabilizar sem confirmação entre os turnos. |
| Interface web local | Validada pelo usuário | A versão Streamlit executa o roteiro e permite baixar a conversa em JSON. |
| Interface desktop | Redesenhada e validada no Linux | Navegação com versão, temas claro e escuro, editor ampliável e conversa selecionável. Veja as [capturas e a validação no Windows](fase-4-interface.md). |
| Executável do Windows | Build concluído | O executável abriu corretamente e inclui o Chromium. O script mantém a versão atual e as duas anteriores. |
| Conversa guiada por IA | Implementada e testada com simulação | Groq e Ollama usam o objetivo e o histórico da sessão para conduzir a conversa. O modelo local `qwen3:4b` foi validado com respostas simuladas. |
| Fila de testes | Implementados e validados com chat local | A fila persistente executa um teste por prompt com o Chromium sem janela e atualiza o JSON a cada resultado. |
| Fundação e estabilidade | Implementada e validada localmente | URL configurável, detecção contextual de loops, motivos de encerramento, horários e armazenamento incremental em SQLite. Veja a [documentação da Fase 1](fase-1-fundacao.md). |
| Histórico | Implementado e validado localmente | Consulta, filtros, comparação, reexecução e importação ou exportação. Veja o [guia do Histórico](fase-5-historico.md). |
| Avaliação | Planejada | Classificar o atendimento por critério e guardar as evidências. |
| Execução paralela | Implementada e testada com simulação | Cada teste abre uma sessão de navegador. Falta confirmar a separação dos históricos pelo chatbot em homologação. |

O teste em homologação confirmou o fluxo observado do AutoSystem. A captura usa os eventos do chat e recorre aos parágrafos visíveis quando necessário. A resposta é considerada concluída após três segundos sem alteração.

## Próximas entregas

1. **Validar o aplicativo Windows em homologação.** Rodar o roteiro do cupom pelo `ChatProbe.exe` e conferir os turnos e o JSON salvo.
2. **Validar Groq e Ollama em homologação.** Conferir as mensagens geradas, as respostas capturadas e o motivo do encerramento.
3. **Validar sessões simultâneas em homologação.** Confirmar que duas conversas paralelas mantêm seus históricos separados no chatbot.
4. **Avaliar e comparar execuções.** Mostrar o resultado por critério, o trecho da conversa que o sustenta e o histórico para regressão.
