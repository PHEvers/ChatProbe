# Instalação e operação

## Pacote portátil
Extraia a pasta completa do ZIP e execute ChatProbe.exe. Mantenha a pasta _internal junto do executável. O pacote contém Python, Playwright e Chromium. Para atualizar, extraia a nova versão em uma pasta separada. O SQLite, as configurações e os provedores ficam no diretório de dados do usuário, fora do pacote.

## Primeiros passos
Em Configurações → Aplicação, informe a URL autorizada e o nome de teste. Em Provedores de IA, escolha o modo. Roteiro JSON usa um roteiro existente e dispensa serviço de IA. Comece com um teste, confira a conversa e depois ajuste a simultaneidade e os intervalos. A Biblioteca guarda prompts e a Fila permite agendar e retomar itens pendentes. Histórico reúne comparação e exportação.

## Groq
Abra https://console.groq.com/keys e crie uma chave. Salve-a no perfil Groq em Configurações → Provedores de IA. Atualize a lista, selecione o modelo e teste a conexão. Testar geração pode consumir cota. RPM e RPD contam requisições por minuto e dia. TPM e TPD contam tokens. Confira os limites da conta em https://console.groq.com/docs/rate-limits. Ao revogar uma chave no painel, substitua-a no ChatProbe.

## Ollama
Use o instalador oficial em https://ollama.com/download/windows. Por exemplo, consulte https://ollama.com/library/qwen3:4b e rode `ollama pull qwen3:4b`, `ollama list`, `ollama run qwen3:4b` e `ollama ps`. Configure http://127.0.0.1:11434 e teste a conexão. A configuração local padrão dispensa chave. O Ollama deve permanecer aberto e o chatbot remoto ainda pode depender de rede. Comece com um modelo menor e um teste. Considere a quantização, contexto, RAM, VRAM e concorrência antes de aumentar o lote.

## Backup e diagnóstico
Em Configurações → Ajuda, crie um ZIP com SQLite e configurações. O backup não inclui as chaves do sistema nem arquivos de relatórios exportados. Restaure com os testes encerrados e outras instâncias fechadas. A restauração valida o manifesto e o banco, cria uma cópia de segurança local e fecha a aplicação ao concluir. Reabra e recadastre chaves que não existam na máquina. O diagnóstico copiável mostra versão, plataforma, integridade do banco e presença do executável Chromium.

## Build e validação Windows
Execute `python -m unittest discover -s tests -v` e `.\build-windows.ps1`. Confira a versão, abra o EXE da pasta extraída e valide o modo JSON, imagem do Chromium, backup, restauração e atualização sobre os dados existentes. A ação manual Pacote Windows gera um ZIP para revisão. O build não publica releases.
