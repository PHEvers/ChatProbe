# Fase 5 do ChatProbe

A aba **Histórico** fica entre **Fila de testes** e **Configurações**. Use `Alt+3` para abrir o Histórico e `Alt+4` para acessar Configurações. A tela Teste organiza os controles à esquerda e mantém o prompt sobre a conversa à direita.

## Pesquisar e abrir resultados

Digite um título, ID, trecho do prompt ou da conversa, produto, provedor, modelo ou motivo de encerramento. Em **Filtros**, combine datas, horário e metadados. Os campos de texto do filtro aceitam valores exatos. Datas sem hora usam o fuso do sistema e abrangem o dia inteiro. Um deslocamento explícito, como `2026-11-25T10:00-03:00`, permite filtrar em outro fuso. O armazenamento usa UTC e a interface exibe o horário local.

A lista abre com os testes mais recentes e carrega até 30 resultados por página. Use **Ordenar por** para trocar entre data, duração, interações e tokens conhecidos. **Limpar** remove a pesquisa e os filtros. Os indicadores representam todos os registros encontrados pelos filtros, incluindo as outras páginas. Uma conclusão descreve o encerramento da execução. A revisão da qualidade continua sendo feita pela leitura das conversas.

Ao selecionar um teste, use **Dados**, **Prompt**, **Conversa** e **Eventos**. O prompt original é somente leitura e pode ser ampliado. Na conversa, selecione o texto para copiar parcialmente ou use **Copiar conversa**. O tempo de resposta observado vai do envio à primeira resposta capturada. O tempo das chamadas de IA inclui o transporte. As esperas do motor são exibidas separadamente quando registradas.

## Reexecutar, adicionar à fila e comparar

**Reexecutar teste** carrega o prompt na aba Teste e recupera produto, URL sanitizada, identificação e opções disponíveis. Use **Revisar** para ajustar a URL, o nome e os controles de execução. O modo e o provedor podem ser alterados nos seletores existentes. O botão **Iniciar teste** verifica a disponibilidade do provedor quando a execução usa IA e pede confirmação. Se o perfil original estiver indisponível, escolha um perfil atual com o modelo apropriado. A nova execução tem ID próprio e referencia o teste de origem.

**Enviar à fila** copia os prompts selecionados com suas referências de origem. Selecione testes com prompt conhecido e modo compatível com a fila. A fila usa as condições escolhidas na tela de execução. **Comparar** abre dois registros lado a lado, com os mesmos painéis de detalhes e um resumo das condições diferentes ou desconhecidas.

## Importar, exportar e excluir

- **Importar JSON** abre relatórios individuais em lista de turnos, lotes com a chave `tests` e exportações do Histórico. A importação valida os turnos, reconhece IDs ou conteúdo duplicado e informa quantos itens foram importados, repetidos ou inválidos.
- **Exportar** grava os registros selecionados em JSON, Markdown com a conversa completa ou CSV de metadados. A janela de salvamento pede confirmação antes de substituir arquivos. Use Ctrl ou Shift para selecionar vários registros da página.
- **Excluir** pede confirmação explícita e protege testes em execução e testes de filas ativas. Arquivos JSON, Markdown e CSV existentes permanecem no disco. O consumo usado pelas cotas continua registrado mesmo quando a referência a um teste excluído é removida.

## SQLite e compatibilidade

O schema 5 acrescenta ao registro de teste o prompt original, título, produto, URL sanitizada, parâmetros, versão, referência de origem e colunas opcionais para a futura Biblioteca. As mensagens e os eventos continuam nas tabelas existentes. O histórico lê a mesma contabilidade usada pelas cotas e a importação não injeta consumo nesse controle. Os índices suportam os principais filtros. Consultas, importação, exportação e exclusão são executadas fora da thread gráfica.

A migração aproveita prompts, títulos, produto e versão disponíveis nos resultados de filas anteriores. Alguns relatórios antigos não contêm prompt original, versão, datas, métricas ou horários por mensagem. O Histórico exibe esses valores como desconhecidos. A data de importação é registrada separadamente e não substitui a data da execução. Registros sem prompt original continuam disponíveis para leitura, comparação e exportação.

O banco continua no diretório local de dados da aplicação e fica fora da pasta do executável. Os campos técnicos são gravados por lista de permissões. A URL histórica omite credenciais, query e fragmento, por isso pode exigir revisão antes de uma reexecução. Prompts e conversas são conteúdo da execução e devem ser revisados antes de serem compartilhados.

## Validação no Windows

1. Na branch da Fase 5, execute `python -m unittest discover -s tests -v` e `.\build-windows.ps1`. Abra `dist\ChatProbe\ChatProbe.exe`.
2. Abra o Histórico em uma instalação com registros existentes. Altere tema e tamanho da janela. Confira filtros, paginação, detalhes e conversas extensas.
3. Importe um JSON antigo duas vezes. Verifique o contador de duplicados e os horários desconhecidos. Exporte uma seleção em JSON, Markdown e CSV.
4. Compare dois registros, carregue um deles na aba Teste e revise o destino antes de executar. Confirme que o novo ID referencia o original. Envie testes compatíveis à fila e confira seus prompts.
5. Abra o Histórico durante uma execução. Confira que a pesquisa permanece acessível e que a exclusão do teste ativo é recusada.

### Capturas do Linux

As capturas abaixo mostram a interface Tk com dados fictícios.

- [Teste com a disposição da referência visual](images/fase-5-teste-referencia.png)
- [Histórico escuro com filtro e conversa](images/fase-5-historico-1366.png)
- [Comparação no tema claro](images/fase-5-comparacao.png)
- [Histórico em janela de 800 × 620](images/fase-5-historico-compacto.png)
