# Local RAG Chat MVP

Этот MVP добавляет простой read-only чат поверх semantic memory.

Цепочка работы:

1. Пользователь задает вопрос.
2. `SemanticRetriever` ищет релевантные chunks в SQLite/FTS базе.
3. `build_rag_prompt()` собирает prompt с вопросом и источниками.
4. `OllamaClient` отправляет prompt в локальную Ollama через `/api/chat`.
5. Ответ и источники показываются в CLI или Streamlit UI.

## Что реализовано

- Локальный клиент Ollama: `linehelper/llm/ollama_client.py`.
- RAG answer layer: `linehelper/llm/answer_generator.py`.
- Streamlit chat UI: `linehelper/ui/streamlit_app.py`.
- CLI smoke-test полного контура: `scripts/smoke_test_local_agent.py`.
- Unit-тесты без реального вызова Ollama.

## Что пока не реализовано

- Нет полноценного агента с инструментами.
- Нет интеграции с 1С.
- Нет генерации КП.
- Нет episodic memory.
- Нет записи в память.
- Нет embeddings/vector search.

Контур только читает локальную semantic memory.

## Защита качества LLM context

Retrieval может найти правильную тему, но вместе с ней вернуть chunks со случайными
совпадениями слов. Например, вопрос про отпуск может найти инструкцию по отпуску,
а рядом добавить общий chunk про задачи или статистики. Если такие chunks попадут
в prompt, локальная модель может ответить по нерелевантной теме.

Поэтому перед отправкой prompt в Ollama используется отдельный отбор context chunks:

- по умолчанию в LLM context попадает максимум 3 chunks;
- chunks сильно ниже top-score отсекаются;
- по умолчанию используется порог `score >= top_score * 0.65`;
- если в вопросе есть сильный anchor-term, например `отпуск`, chunks без этого
  anchor в названии, разделе, смысловом блоке или тексте не попадают в LLM context,
  если есть chunks с anchor;
- подготовлены anchor-группы для `отпуск`, `зрс`, `цкп`, `командировка`,
  `договор`, `распоряжение`.

Это не меняет базу знаний и не правит curated chunks. Фильтр работает только перед
сборкой prompt для модели.

## Quality hardening после ручного исследования

После ручной проверки живых вопросов поверх retrieval добавлен простой слой query
understanding без внешних моделей. Он не меняет `curated_chunks.jsonl`, raw docs и
SQLite, а работает только во время ответа.

Что делает слой:

- определяет intent для вопросов про компанию в целом, документооборот, отпуск,
  ЗРС и ЦКП;
- для вопроса `Чем занимается компания?` предпочитает chunks из `ИП-0002 Цели и
  замыслы компании Serviceline`, `ИП-0003 ЦКП SERVICELINE` и блоки про цель
  компании, а не случайные функции подразделений;
- для вопросов про документооборот предпочитает `ИП-0006 Документооборот`,
  `1С ДО` и инструкции согласования;
- если пользователь спрашивает `КП` без `ЦКП`, возвращает уточнение: КП как
  коммерческое предложение или ЦКП как ценный конечный продукт компании;
- если вопрос явно вне корпоративной базы знаний или найденные chunks слишком
  слабые, LLM не вызывается и источники ответа остаются пустыми.

UI разделяет:

- `Источники ответа` - только chunks, реально отправленные в LLM context или
  использованные для ответа;
- `Диагностика` - retrieved/rejected candidates, которые полезны разработчику,
  но не являются доказательствами ответа.

Ограничение: это пока прозрачные эвристические правила, а не полноценный intent
classifier. Их нужно расширять по мере появления новых ручных failure cases.

## Ollama

Ожидаемые настройки:

```powershell
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_MODEL="qwen2.5:14b"
```

Проверка Ollama вручную:

```powershell
$body = @{
  model = "qwen2.5:14b"
  stream = $false
  messages = @(
    @{ role = "user"; content = "Ответь по-русски: что такое тест?" }
  )
  options = @{
    temperature = 0
    num_predict = 100
  }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Uri "http://localhost:11434/api/chat" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

Используется `/api/chat`, а не `/api/generate`, потому что MVP сразу работает в chat-формате: system message + user prompt. Это ближе к будущему интерфейсу чата и проще расширяется.

UTF-8 важен, потому что русские вопросы и корпоративные источники должны уходить в Ollama без повреждения кодировки. Python-клиент отправляет JSON body как UTF-8 и выставляет `Content-Type: application/json; charset=utf-8`.

## Env-переменные

```text
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_TIMEOUT_SECONDS=180
OLLAMA_TEMPERATURE=0
OLLAMA_NUM_PREDICT=700
RAG_CONTEXT_LIMIT=3
RAG_CONTEXT_SCORE_RATIO=0.65
```

`.env` можно использовать локально, но файл `.env` не должен попадать в Git.

## CLI smoke-test

```powershell
cd "C:\Users\Nikolai Paliy\work\projects\linehelper"

.\.venv\Scripts\python.exe scripts\smoke_test_local_agent.py
```

Если Codex-процесс не запускает `.venv`, можно использовать bundled Python:

```powershell
$PY="C:\Users\Nikolai Paliy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $PY scripts\smoke_test_local_agent.py
```

## Streamlit UI

```powershell
cd "C:\Users\Nikolai Paliy\work\projects\linehelper"

.\.venv\Scripts\streamlit.exe run linehelper\ui\streamlit_app.py
```

Альтернатива:

```powershell
.\.venv\Scripts\python.exe -m streamlit run linehelper\ui\streamlit_app.py
```

## Проверки

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test_semantic_retrieval.py
.\.venv\Scripts\python.exe scripts\smoke_test_rag_prompt.py
.\.venv\Scripts\python.exe scripts\smoke_test_semantic_memory.py
.\.venv\Scripts\python.exe scripts\smoke_test_semantic_ingest.py
.\.venv\Scripts\python.exe -m pytest -q tests scripts\tests --basetemp .venv\pytest-tmp -p no:cacheprovider
```

`scripts/smoke_test_local_agent.py` и Streamlit UI требуют запущенную Ollama и локальную модель `qwen2.5:14b`.

## Git safety

В Git не должны попадать:

- `.env`;
- `.venv`;
- `data/raw_docs/*`;
- `data/memory/*.db`;
- `data/semantic_index/curated_chunks.jsonl`;
- backups;
- временные файлы и pytest tmp.
