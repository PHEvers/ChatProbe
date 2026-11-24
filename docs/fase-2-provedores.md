# Fase 2 do ChatProbe

Em **Configurações → Provedores de IA → Gerenciar provedores**, selecione uma predefinição e salve um perfil. Configure o modelo pelo nome ou use **Atualizar modelos** e escolha da lista. A URL base deve incluir o caminho de versão quando o serviço o exigir, como `https://api.openai.com/v1`. Para a API personalizada, o ChatProbe acrescenta `/models` e `/chat/completions` ao endereço base. A temperatura fica opcional e o tempo limite é por requisição.

Use **Testar conexão** para verificar a listagem e o acesso ao modelo selecionado. Quando o serviço não publica modelos por API, informe o identificador manualmente e use **Testar geração**. Esse teste usa um prompt fictício e pode consumir uma requisição ou créditos. O Ollama padrão usa `http://127.0.0.1:11434` sem chave. Um servidor Ollama autorizado pode usar outro endereço. Use HTTPS para enviar chaves a servidores remotos. Os adaptadores Gemini e Claude são experimentais e foram verificados com respostas simuladas. A autenticação e a geração com contas reais ainda precisam ser validadas.

Perfis podem ser renomeados, duplicados ou excluídos. Cada duplicação recebe um identificador novo e começa sem chave. No Windows, **Salvar chave** associa a credencial ao perfil no Gerenciador de Credenciais. Em outros sistemas, a chave vale durante a sessão atual. As chaves Groq já cadastradas permanecem no armazenamento anterior. No perfil Groq original, o botão **Reutilizar chave Groq** permite selecionar uma chave existente. O catálogo inicial importa o modelo Ollama salvo e preserva o modo de execução anterior. A configuração de perfis fica no arquivo local `providers.json`.

Selecione o modo **Provedor de IA** nas preferências de execução. Nas abas **Teste** e **Testes noturnos**, escolha o perfil da execução ou mantenha o padrão. Os modos Groq e Ollama anteriores e o modo Roteiro JSON continuam disponíveis.

## Integração e registros

O runner usa a operação comum de geração, que retorna ação, texto e métricas disponíveis. Os adaptadores normalizam códigos de erro e descartam o conteúdo da resposta de erro antes de apresentá-la. As requisições autenticadas recusam redirecionamentos. O adaptador Chat Completions pede JSON pelo prompt para funcionar também em serviços que não implementam `response_format`. O Gemini usa esquema de resposta próprio, o Ollama usa `format` e o Claude recebe a instrução de sistema separada das mensagens.

Cada lote usa a configuração e a credencial selecionadas no momento da partida. Os relatórios guardam nome, ID, protocolo, modelo, temperatura, timeout e contagens acumuladas de tokens quando fornecidas. O SQLite migra o schema da Fase 1 para a versão 3 e guarda o mesmo retrato do perfil e do consumo, com campos nulos para registros anteriores. A comparação contextual de mensagens e os horários da Fase 1 continuam ativos. A URL base e as credenciais ficam fora dos registros de execução.

## Validação

Execute `python -m unittest discover -s tests -v`. No Windows, gere o pacote com `.\build-windows.ps1`, abra o executável e valide a criação e duplicação de perfis, o uso de uma chave legada, a listagem, a geração mínima e a seleção independente por aba. Confira os campos `provider` e `token_usage` no JSON e rode um roteiro JSON para verificar o fluxo sem provedor.
