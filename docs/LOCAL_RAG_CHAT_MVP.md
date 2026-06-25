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
- распознает простые comparison/multi-intent вопросы по фразам вроде
  `это то же самое`, `это отпуск`, `чем отличается`, `одно и то же`, а также
  по нескольким anchor terms в одном вопросе;
- для comparison-вопросов не применяет жесткий single-anchor filter: вместо
  этого делает небольшие retrieval-запросы по каждой активной теме и выбирает
  представителей нескольких групп, например `Работа с задачами` + отпуск или
  `ИП-0005 Распоряжения` + отпуск;
- для вопроса `Чем занимается компания?` предпочитает chunks из `ИП-0002 Цели и
  замыслы компании Serviceline`, `ИП-0003 ЦКП SERVICELINE` и блоки про цель
  компании, а не случайные функции подразделений;
- для вопросов про документооборот предпочитает `ИП-0006 Документооборот`,
  `1С ДО` и инструкции согласования;
- разделяет intents `kp_ambiguous`, `kp_commercial_offer` и `ckp`: если
  пользователь спрашивает отдельное `КП` без уточнений, возвращает clarification;
  если пишет `КП как коммерческое предложение`, не уточняет повторно и честно
  сообщает, что в базе нет отдельной полной инструкции по коммерческим
  предложениям; если пишет `ЦКП` или `ценный конечный продукт`, использует
  `ИП-0003 ЦКП SERVICELINE`;
- если вопрос явно вне корпоративной базы знаний или найденные chunks слишком
  слабые, LLM не вызывается и источники ответа остаются пустыми.

Перед сборкой prompt работает no-answer gate. Он проверяет, что выбранные chunks
действительно дают тематическую опору для вопроса, а не просто содержат случайные
совпадения вроде `новый`, `работы`, `сколько` или одиночное слово из OCR. Если
достаточных источников нет, ответ возвращается без вызова Ollama:

`В базе знаний Serviceline нет точного ответа на этот вопрос. Похоже, вопрос не относится к корпоративным регламентам, инструкциям, оргструктуре или документообороту.`

В таком ответе `response_kind="no_answer"`, `sources=[]`, `chunks_used=0`, а
retrieval-кандидаты остаются только в `diagnostic_candidates`.

UI разделяет:

- `Источники ответа` - только chunks, реально отправленные в LLM context или
  использованные для ответа;
- `Диагностика` - retrieved/rejected candidates, которые полезны разработчику,
  но не являются доказательствами ответа.

Источники являются ответственностью UI, а не LLM. Prompt запрещает модели
добавлять в тело ответа разделы `Источники`, `Источник` или `Источники ответа`
и перечислять названия документов. Если локальная модель все равно добавит
такой хвост в конце ответа, answer layer аккуратно удалит trailing-блок
источников, сохранив структурированные `sources` для отображения в интерфейсе.

Ограничение: это пока прозрачные эвристические правила, а не полноценный intent
classifier. Их нужно расширять по мере появления новых ручных failure cases.

## LLM Query Analyzer: experimental stage

Добавлен первый безопасный этап будущей архитектуры: `linehelper/rag/query_analyzer.py`.
Он использует локальную Ollama/LLM, чтобы преобразовать живой пользовательский вопрос в
структурированный `QueryPlan` для диагностики, тестов и будущего подключения к retriever.

Важно: Query Analyzer пока не подключен к боевому RAG-ответу. Основной ответ пользователя
продолжает работать по старой схеме:

`user question -> rule-based routing -> retriever -> no-answer gate -> prompt_builder -> Ollama answer`

`QueryPlan` содержит:

- `intent` - нормализованный смысл вопроса, например `org_structure`, `company_ckp`,
  `ambiguous_abbreviation`, `document_loss`;
- `normalized_question` - переформулированный вопрос для поиска;
- `query_expansions` - расширения запроса и синонимы;
- `preferred_sources` - очевидные предпочтительные источники, если они известны;
- `answer_type` - ожидаемый тип ответа;
- `needs_clarification` и `clarification_question` - флаг и текст уточнения;
- `confidence` и `notes` - диагностическая уверенность и пояснение.

Будущий целевой пайплайн:

`user question -> Query Analyzer -> QueryPlan -> Retriever -> Evidence Gate -> Answer LLM`

Риски:

- дополнительная задержка на отдельный LLM-вызов;
- возможный битый JSON от локальной модели;
- ошибка intent или слишком уверенная нормализация вопроса.

Меры безопасности текущего этапа:

- strict schema с фиксированными `intent` и `answer_type`;
- rule-based fallback при недоступной Ollama или невалидном JSON;
- Python validation и нормализация полей;
- `KNOWN_SOURCE_TITLES` как общий allowlist известных source titles;
- `INTENT_SOURCE_COMPATIBILITY` как отдельная карта совместимости источников с intent;
- старый pipeline остается неизменным и не зависит от Query Analyzer.

Одного `KNOWN_SOURCE_TITLES` недостаточно: источник может быть реальным, но семантически
неподходящим для intent. Например, `ИП-0004 Структура ЗРС` является известным источником,
но не должен становиться `preferred_sources` для отпуска, статистики или ответственности
отделов. Поэтому post-validation сначала удаляет неизвестные source titles, затем удаляет
известные, но несовместимые с intent. Если compatibility set для intent пустой, это значит,
что достоверного preferred source для этого intent пока нет, и `preferred_sources=[]`.

Добавлены диагностические intent:

- `attendance_absence` - опоздание, болезнь, отсутствие, невыход на работу. Пока без
  достоверного preferred source и без полноценного procedural answer.
- `one_c_operational_lookup` - будущие операционные вопросы к 1С: цены, остатки, статус
  заказа, счета, контрагенты, отгрузки, номенклатура. Этот intent только помечает будущий
  тип запроса; Query Analyzer не выполняет поиск в 1С и не подключен к 1С-интеграции.

Ручная диагностика:

```powershell
$env:OLLAMA_ANALYZER_MODEL="qwen2.5:3b"
.\.venv\Scripts\python.exe scripts\smoke_test_query_analyzer.py
```

Если локальная Ollama недоступна или вернула битый JSON, smoke-скрипт печатает причину и
показывает fallback `QueryPlan`, а не падает с непонятной ошибкой.

### Query Analyzer test strategy

Для Query Analyzer используется два уровня проверок.

Strict regression tests в `tests/test_query_analyzer.py` фиксируют уже найденные ошибки:
разделяют `company_identity` и `org_structure`, защищают смысл `ЦКП`, требуют уточнение
для короткого `КП`, запрещают выдуманные `preferred_sources` и не дают unsupported темам
вроде потери документа или IT-запроса превращаться в полноценную процедуру без источника.
Эти тесты unit-level и не требуют реальной Ollama.

Strict smoke `scripts/smoke_test_query_analyzer.py` проверяет короткий набор базовых живых
вопросов перед коммитом. Он падает на критичных ошибках: неправильный intent для известных
регрессий, запрещенный `answer_type`, источник вне allowlist или известный источник,
несовместимый с intent.

Exploratory smoke `scripts/smoke_test_query_analyzer_exploratory.py` прогоняет более широкий
набор неидеальных пользовательских формулировок и печатает WARN-флаги: низкую уверенность,
пустые query expansions, возможный неверный intent, процедуру без источника, off-topic с
источником или плохое расширение ЦКП. WARN не всегда означает баг; это сигнал для ручного
разбора и следующей итерации качества. Скрипт завершает работу с ошибкой только на критичных
нарушениях, например `CKP_BAD_EXPANSION`, `OFF_TOPIC_WITH_SOURCE`, источнике вне allowlist
или crash.

Query Analyzer остается экспериментальным диагностическим слоем и все еще не подключен к
боевому RAG-ответу.

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
