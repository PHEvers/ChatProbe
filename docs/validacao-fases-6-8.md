# Validação das fases 6 a 8

A suíte completa passou com 68 testes em Linux com Tk executado em um display virtual. Foram usados bancos temporários e respostas fictícias. O teste de interface abriu a Biblioteca e a Ajuda e executou pelo Histórico uma avaliação determinística com evidência de mensagem. O teste semântico usa um provedor simulado para verificar o Rate Limiter, as contagens e a rejeição de citações inexistentes.

- [Biblioteca com editor e revisão registrada](images/fases-6-8-biblioteca.png)
- [Ajuda com assistente, cópia e restauração](images/fases-6-8-ajuda.png)
- [Avaliação determinística, nota e mensagem citada](images/fases-6-8-avaliacao.png)

A Biblioteca exporta JSON, Markdown e TXT. JSON e o Markdown versionado preservam os campos dos prompts. TXT guarda somente conteúdo com separadores. A importação oferece edição da prévia, seleção e escolha do tratamento para duplicatas.

## Windows

Na branch de avaliação, rode `python -m unittest discover -s tests -v` e `.\build-windows.ps1`. Extraia o ZIP gerado em `dist` e confira a abertura do executável e a presença do Chromium. Use um perfil fictício para validar assistente, Ajuda, backup e restauração. Na Biblioteca, importe um arquivo com dois prompts, edite um deles, copie para a fila e confira a revisão no Histórico. Use os registros recém-criados para revisar uma avaliação, fixar uma baseline e comparar a seleção. Depois valide a avaliação semântica com uma rubrica e conversas autorizadas por meio de um provedor de teste.

O GitHub não expôs uma execução de CI para a integração usada nesta sessão. O pacote nativo e a qualidade da avaliação com modelos reais continuam pendentes. O resultado local confirma comportamento determinístico e validação de contrato, sem estimar precisão semântica.
