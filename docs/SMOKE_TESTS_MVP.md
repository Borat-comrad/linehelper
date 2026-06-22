# Smoke-тесты MVP для semantic memory

Эти проверки нужны для этапа перед подключением модели. Сейчас мы не вызываем
Ollama, не используем LLM-клиент, не строим embeddings и не делаем UI.

Пользователь пока видит не готовый ответ модели, а подготовку к RAG:

- какие chunks нашлись через FTS;
- насколько релевантны TOP-результаты;
- из каких документов, разделов и страниц они пришли;
- какой релевантный фрагмент попадет в prompt будущей модели.

## Technical PASS и quality PASS

Технический PASS означает: поиск не упал, БД открылась, запросы что-то нашли.
Этого мало для RAG, потому что TOP 1 может быть формально найденным, но шумным.

Quality PASS означает: ожидаемый корпоративный источник поднялся в нужный TOP.
Например, вопрос про планирование на неделю должен поднимать
`Регламент по планированию на неделю`, а не общий навигатор с похожими словами.

## Статусы retrieval

`RELEVANT_TOP1` означает, что ожидаемый источник стоит первым.

`RELEVANT_TOP3` означает, что ожидаемый источник найден в первых трех.

`RELEVANT_TOP5` означает, что ожидаемый источник найден в первых пяти.

`WEAK` означает, что что-то найдено, но ожидаемый источник не попал в нужный TOP.
Такой кейс нужно смотреть и улучшать retrieval или curated chunks.

`REVIEW` означает, что скрипт отработал, но есть слабые места для ручного
разбора. Это не авария, но перед подключением модели лучше разобраться.

`FAIL` означает настоящую проблему: нет БД, нет semantic chunks, есть пустой
важный retrieval-кейс или prompt builder упал.

## Почему важен Matched excerpt

Обычный snippet часто показывает начало chunk. Если начало шумное, пользователь
видит оргструктуру, номера отделений или служебный текст, хотя нужный ответ
есть дальше.

`Matched excerpt` показывает фрагмент вокруг совпавших терминов. Именно этот
фрагмент важен для будущей модели: чем чище excerpt, тем меньше шанс получить
плохой ответ.

## Почему модель пока не подключаем

Если retrieval приносит шум, модель будет красиво пересказывать шум. Поэтому
сначала нужно добиться качественного поиска и понятного prompt, а уже потом
подключать Ollama или другой LLM-клиент.

## Как запускать пользователю

Откройте PowerShell:

```powershell
cd "C:\Users\Nikolai Paliy\work\projects\linehelper"

.\.venv\Scripts\python.exe scripts\smoke_test_semantic_retrieval.py

.\.venv\Scripts\python.exe scripts\smoke_test_rag_prompt.py

.\.venv\Scripts\python.exe scripts\smoke_test_semantic_memory.py

.\.venv\Scripts\python.exe scripts\smoke_test_semantic_ingest.py

.\.venv\Scripts\python.exe -m pytest -q --basetemp data\semantic_index\pytest-tmp -p no:cacheprovider
```

## Fallback для Codex/bundled Python

В процессе Codex `.venv` может не запускаться из-за особенностей процесса, даже
если у пользователя она вручную работает нормально. Тогда можно использовать
bundled Python:

```powershell
$PY="C:\Users\Nikolai Paliy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

& $PY --version

& $PY scripts\smoke_test_semantic_retrieval.py

& $PY scripts\smoke_test_rag_prompt.py

& $PY scripts\smoke_test_semantic_memory.py

& $PY scripts\smoke_test_semantic_ingest.py
```

Для pytest через bundled Python:

```powershell
$PY="C:\Users\Nikolai Paliy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = (Resolve-Path ".venv\Lib\site-packages").Path

& $PY -m pytest -q --basetemp data\semantic_index\pytest-tmp -p no:cacheprovider
```

## Если БД отсутствует

Если файла `data\memory\linehelper_memory.db` нет, сначала импортируйте curated
chunks:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_semantic_documents.py --import-curated --curated-path data\semantic_index\curated_chunks.jsonl
```

## Что не должно попасть в Git

Не добавляйте в Git локальные данные и временные файлы:

- `data\raw_docs\*`
- `data\memory\*.db`
- `data\semantic_index\*`
- preview-файлы
- manifest-файлы
- `.env`
- временные папки pytest, например `data\semantic_index\pytest-tmp`
