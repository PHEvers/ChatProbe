# Biblioteca de prompts

A aba Biblioteca guarda prompts no SQLite local. Cadastre um título, conteúdo, modo, produto, tags e resultado esperado. O editor Ampliar preserva espaços e quebras de linha. Salvar um conteúdo alterado cria uma revisão e Versões abre snapshots anteriores no editor. Excluir uma coleção deixa seus prompts sem coleção. Excluir prompts preserva o Histórico.

Use **Abrir no Teste** para preencher o editor ou **Enviar à fila** para copiar os prompts selecionados. Revise as condições e inicie pelos controles de execução existentes. O Histórico permite consultar execuções pelo ID do prompt. Novas execuções guardam referência à versão utilizada.

A importação aceita um roteiro JSON, um documento `{"version":1,"prompts":[{"title":"Exemplo","content":"Texto"}]}` ou arquivos TXT e Markdown. Em texto, use a linha `--- CHATPROBE ---` para separar prompts. Confira a prévia antes de confirmar. Duplicatas exatas na coleção de destino são ignoradas. A exportação atual usa JSON.
