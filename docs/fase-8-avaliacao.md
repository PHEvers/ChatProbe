# Avaliação de atendimentos

Em Histórico, selecione registros e clique em Avaliações. A janela permite editar critérios JSON, associar uma referência textual, selecionar um perfil avaliador e consultar avaliações anteriores. Antes de enviar dados a um provedor escolhido há confirmação explícita. O avaliador usa o Rate Limiter compartilhado e mantém seu consumo separado das chamadas do teste.

Cada critério requer `id`, `title`, `type` e `value`. Os tipos são `contains`, `absent`, `min_turns`, `status`, `reason`, `event` e `semantic`. O campo opcional `when` limita a aplicabilidade à presença desse texto no diálogo. Use `weight` para ponderação e `required` para obrigatoriedade.

O código pontua 100% para atendido, 50% para parcial e 0% para não atendido. Os pesos aplicáveis com evidência formam o denominador. A ausência de evidência conduz a inconclusivo, exceto quando uma falha obrigatória conhecida já impede aprovação. Uma falha técnica da execução deixa a avaliação inconclusiva. Critérios semânticos exigem referência textual e citações literais apontando para uma mensagem existente.

A revisão humana é anexada separadamente e mantém o resultado original. Use Fixar baseline e Comparar para classificar resultados da seleção por prompt. Alterações de prompt, versão, parâmetros, avaliador ou referência tornam a comparação não comparável. A exportação pode ser feita em JSON ou Markdown.

Em **Em massa**, prepare registros pendentes dos filtros atuais, do lote ou da execução de coleção. Para automatizar verificações determinísticas após novos testes, selecione **Avaliação local**, defina `"automatic_local": true` no JSON e salve os critérios. A avaliação semântica exige confirmação manual por execução da avaliação. Valide com critérios autorizados e mensagens fictícias antes de enviar referências internas a qualquer serviço.
