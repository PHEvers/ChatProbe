# Fase 4 do ChatProbe

O desktop usa Tk/ttk. O cabeçalho reúne **Teste**, **Fila de testes** e **Configurações** à esquerda, com **ChatProbe** e a versão à direita. Use `Alt+1` e `Alt+2` para abrir Teste e Fila de testes, e `Tab` para navegar pelos controles. Na versão com [Histórico](fase-5-historico.md), `Alt+3` abre Histórico e `Alt+4` abre Configurações. O estado da execução aparece com texto, cor e barra de progresso.

## Teste e fila

Em **Teste**, as opções da execução ficam acima do editor. A conversa tem seleção de resultado, painel ampliável e exportação JSON. Selecione texto para copiar com `Ctrl+C` ou use **Copiar** para levar a transcrição completa com os horários disponíveis.

O mesmo editor é usado em **Teste** e **Fila de testes**. Ele oferece quebra de linha, rolagem, título opcional, contagem de caracteres e edição ampliada. Use **Ampliar editor** ou `Ctrl+Shift+E`. A janela ampliada transfere o texto e o título de volta ao ser fechada. Nos modos de IA, a estimativa usa aproximadamente quatro bytes UTF-8 por token. Para prompts extensos, confira a janela de contexto publicada pelo modelo. Ela varia por provedor e pode incluir a instrução de sistema e o histórico além do texto do editor.

Em **Fila de testes**, selecione um item para editar ou use **Subir** e **Descer** para reordenar. A fila mostra o título ou um resumo, enquanto a execução usa o conteúdo integral. Títulos opcionais ficam separados da lista de prompts nas preferências, de modo que as filas antigas abrem na mesma ordem. A mesma tela reúne intervalos, limites, agendamento, recuperação, progresso e controles de execução.

## Configurações

- **Aplicação** reúne a URL de atendimento e o nome padrão. Também exibe o CNPJ de triagem usado pelo runner.
- **Provedores de IA** reúne o modo de execução, modelos, gerenciador e credenciais existentes no Windows.
- **Consumo** reúne o acesso aos padrões de controle de execução e as informações sobre comportamento da conversa. Limites globais por provedor e modelo ficam no gerenciador.
- **Armazenamento** permite escolher a pasta JSON e informa o uso de SQLite e do fuso do sistema.
- **Aparência** guarda a escolha de tema claro ou escuro.

## Versão e build

`VERSION` é a fonte de versão em formato `MAJOR.MINOR.PATCH`. O projeto não tinha tags nem releases versionados antes desta mudança. O primeiro valor declarado é `0.1.0`. A interface lê o arquivo local. Nos relatórios em lista, `application_version` fica nos metadados do primeiro turno. Nos relatórios agrupados, fica na raiz e nos resultados dos testes novos.

`build-windows.ps1` inclui o arquivo no pacote e gera o recurso nativo de versão do executável a partir da mesma fonte. O script confere o arquivo empacotado e o campo Windows **Versão do produto** antes de mover o pacote para o destino. A rotação continua mantendo o pacote atual e até dois anteriores.

## Evidências e validação

As imagens são capturas do aplicativo Tk no Linux com prompts e respostas de demonstração. As dimensões indicam a área cliente da janela.

| Captura | Dimensão |
| --- | --- |
| [Teste no tema escuro](images/fase-4-teste-1366.png) | 1366 × 768 |
| [Fila no tema claro](images/fase-4-fila-1920.png) | 1920 × 1080 |
| [Fila em janela reduzida](images/fase-4-fila-reduzida.png) | 800 × 620 |
| [Configurações em janela maximizada](images/fase-4-config-maximizada.png) | 2000 × 1126 |

No Linux, a suíte gráfica também verifica o acesso aos controles em escala Tk aumentada. No Windows, valide a escala real do sistema e o pacote nativo conforme os passos abaixo.

1. No PowerShell, execute `python -m unittest discover -s tests -v` e `.\build-windows.ps1`. Abra `dist\ChatProbe\ChatProbe.exe`.
2. Compare a tag do cabeçalho, o conteúdo de `VERSION` e a versão do produto nas propriedades do executável.
3. Use as resoluções 1366 × 768 e 1920 × 1080, nas escalas de 100% e 125%. Alterne temas, maximize e reduza a janela. Confira o acesso aos botões, à edição ampliada e aos formulários com rolagem.
4. Abra preferências antigas, confirme a ordem da fila e mova um prompt longo que contém acentos, espaços e quebras de linha. Amplie, edite e feche o editor. Confira o texto salvo.
5. Execute um roteiro autorizado individualmente e dois itens em paralelo na fila. Teste pausa, retomada e parada, consulte os resultados e exporte o JSON. Confira os horários e a versão. Em ambiente autorizado, repita com os provedores que usa.
