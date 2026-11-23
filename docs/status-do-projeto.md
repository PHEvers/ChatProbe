# Andamento do ChatProbe

Atualizado em 23/11/2026.

O ChatProbe já executa um roteiro JSON e registra uma conversa real com o AutoSystem em homologação. A janela desktop foi exercitada com uma conversa simulada, e o executável do Windows foi gerado. Falta validar o cenário do cupom pelo executável.

| Entrega | Situação | Evidência ou trabalho restante |
| --- | --- | --- |
| Abertura do chat e triagem | Validada em homologação | O script encontrou o chat, enviou o CNPJ de teste, selecionou Suporte Técnico e AUTOSYSTEM e aguardou o pedido do motivo do contato. A abertura faz até cinco tentativas de localizar o botão, com quatro recargas. |
| Envio e captura da conversa | Validados em homologação | O teste de cupom registrou dois turnos na mesma sessão. O relatório JSON local contém duas mensagens enviadas e uma resposta do bot para cada uma. |
| Roteiro JSON com respostas condicionais | Validado em homologação | O exemplo de cupom envia o nome de teste quando a resposta contém “seu nome”. A captura aguarda a resposta estabilizar sem confirmação entre os turnos. |
| Interface web local | Validada pelo usuário | A versão Streamlit executa o roteiro e permite baixar a conversa em JSON. |
| Interface desktop | Implementada e testada com simulação | A janela nativa permite editar o roteiro, executar, acompanhar a execução, ler a conversa e salvar o JSON. Falta rodar esse fluxo com o chat real a partir da janela. |
| Executável do Windows | Build concluído | O script PowerShell gerou `dist/ChatProbe/ChatProbe.exe` e confirmou que o Chromium acompanha o pacote. Falta executar o cenário em homologação pelo aplicativo. |
| Conversa guiada por IA | Planejada | Receber o objetivo do teste em linguagem natural, ler cada resposta do chatbot e decidir a próxima ação de acordo com o objetivo e o histórico da conversa. |
| Avaliação e histórico | Planejados | Classificar o atendimento por critério e guardar evidências e execuções anteriores. O JSON atual registra a conversa. |
| Execução paralela | Planejada | Verificar o isolamento entre sessões em homologação antes de executar mais de uma conversa por vez. |

O teste em homologação foi confirmado para o fluxo observado do AutoSystem. A leitura usa um seletor de parágrafos e a conclusão da resposta é inferida após três segundos sem alteração. Vale revalidar esses pontos quando surgirem novos formatos de mensagem.

## Próximas entregas

1. **Consolidar o protótipo no Git.** A referência local de `origin/main` ainda aponta para o commit inicial `0974cd4`. O código do executor, as interfaces, o roteiro de exemplo e o script de build estão no workspace e aguardam commit.
2. **Validar o aplicativo Windows.** Rodar o roteiro do cupom pelo `ChatProbe.exe`. A entrega passa quando a janela mostra os dois turnos reais e salva um JSON com as respostas esperadas.
3. **Conduzir uma conversa a partir de um prompt em português.** Abrir o chat, ler a resposta e usar o objetivo e o histórico para decidir se a IA envia uma mensagem, seleciona uma opção disponível ou encerra o teste. Guardar a conversa e o motivo do encerramento.
4. **Avaliar e comparar execuções.** Mostrar o resultado por critério, o trecho da conversa que o sustenta e o histórico para regressão.
5. **Testar sessões simultâneas.** Confirmar que duas conversas mantêm seus contextos separados e estabelecer um limite de sessões.
