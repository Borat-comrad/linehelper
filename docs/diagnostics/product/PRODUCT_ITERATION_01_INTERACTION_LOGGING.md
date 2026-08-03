# Продуктовая итерация 01. Interaction Logging, Feedback и эксплуатационная аналитика

## Исходная эксплуатационная проблема

LineHelper формировал диагностируемые RAG-ответы, но не сохранял структурированную историю реальных взаимодействий. Нельзя было воспроизводимо оценить частые вопросы, режимы ответа, latency, использованные источники, причины отрицательной пользовательской оценки и случаи, пригодные для будущих regression tests.

## Место analytics в pipeline

Interaction logging добавлен как best-effort side effect строго после формирования окончательного `RagAnswer`:

```text
RAG pipeline
→ final RagAnswer
→ best-effort InteractionLogger
→ возврат неизменённого ответа пользователю
```

Логгер не участвует в QueryPlan, clarification, conversation resolution, retrieval, context composition, evidence assessment или Grounded Answer Contract. Ошибка записи не запускает повторный LLM-вызов и не меняет режим ответа.

## Разделение данных

- Semantic memory содержит корпоративные документы и стабильные знания.
- Episodic memory предназначена только для отдельно проверенного практического опыта.
- Interaction analytics содержит сырые обращения, итоговые ответы, whitelisted diagnostics, sources, latency и feedback.

Interaction analytics использует отдельную локальную базу `data/analytics/linehelper_interactions.db`. Сырые interactions и feedback не записываются автоматически в semantic или episodic memory.

## SQLite schema

Schema version: `1`.

Таблицы:

- `analytics_metadata` — версия схемы и timestamp последнего retention cleanup;
- `interactions` — UUID interaction, session metadata, original/resolved question, финальный QueryPlan, режим ответа, whitelisted diagnostics, chunk ID lists, доступные latency и технический статус;
- `interaction_sources` — один упорядоченный source record на chunk в рамках interaction, supporting/rendered flags и requirement IDs без полного текста chunk;
- `interaction_feedback` — append-only positive/negative events с reason и необязательным comment.

Используются `WAL`, `foreign_keys=ON`, `busy_timeout`, idempotent `ensure_schema`, индексы по времени, session, answer mode и feedback. Связанные sources и feedback удаляются через foreign-key cascade.

## Конфигурация

Поддержаны настройки:

- `LINEHELPER_ANALYTICS_ENABLED=true`;
- `LINEHELPER_ANALYTICS_DB_PATH=data/analytics/linehelper_interactions.db`;
- `LINEHELPER_ANALYTICS_RETENTION_DAYS=90`;
- `LINEHELPER_ANALYTICS_STORE_TEXT=true`;
- `LINEHELPER_ANALYTICS_REDACT_PII=true`;
- `LINEHELPER_ANALYTICS_QUEUE_SIZE=1000`;
- `LINEHELPER_ANALYTICS_USER_HASH_SECRET=`.

При отключённой analytics используется `NullInteractionLogger`, и база не создаётся. Относительный DB path разрешается от корня проекта. При отсутствии user hash secret открытый user ID не сохраняется. Retention со значением `0` или меньше отключает автоматическое удаление.

## Privacy и sanitizer

Перед сохранением текстовых полей deterministic sanitizer маскирует email, телефонные номера и явные секреты в конструкциях `token=`, `password=`, `api_key=` и `authorization=`. При `LINEHELPER_ANALYTICS_STORE_TEXT=false` вопросы, ответы и comments сохраняются как `NULL`, а metadata, diagnostics, IDs и latency остаются доступны.

Diagnostics сериализуются через явные whitelist-функции. Не сохраняются prompt, system prompt, chain-of-thought, environment variables, HTTP headers, credentials или полные тексты retrieved chunks. Sanitizer является минимальным MVP-контуром и не заменяет DLP.

## Failure isolation

MVP использует bounded in-memory queue и один daemon writer thread. SQLite-записи выполняются короткими транзакциями; connections закрываются явно. При переполнении очереди или ошибке базы interaction может быть пропущен, но готовый `RagAnswer` возвращается без изменения. Ошибка логгера попадает только в штатный application logger в санитизированном виде.

CLI вызывает `flush()` и `close()` при завершении. Public API существующих одноходовых вызовов остаётся совместимым; в `RagAnswer` добавлены только optional analytics-поля.

## UI feedback

Существующий Streamlit UI использует нативный `st.feedback("thumbs")` под ответом, только когда доступен `interaction_id`.

- 👍 сохраняет append-only event `positive` и показывает ненавязчивое подтверждение.
- 👎 открывает форму с одной из семи причин: неверный, неполный ответ, неверные источники, документ не найден, вопрос понят неправильно, слишком долго или другое; comment необязателен.
- Ошибка feedback storage показывает короткое сообщение, не скрывает ответ и не повторяет RAG-запрос.
- Новый диалог создаёт новый analytics session ID.

CLI логирует interactions при включённой analytics, но не запрашивает оценку после каждого ответа. Debug output может показать `interaction_id`, `analytics_logged` и санитизированную `analytics_error`.

## Retention

Default retention — 90 дней. `cleanup_expired()` атомарно удаляет устаревшие interactions и связанные sources/feedback. Startup cleanup выполняется best-effort не чаще одного раза в сутки; ручной cleanup доступен в export script. Cleanup не запускается перед каждым вопросом.

## Export script

Создан `scripts/export_interaction_stats.py`. Он поддерживает DB/date filters, JSON, CSV directory и ручной retention cleanup. Минимальный пример:

```powershell
.\.venv\Scripts\python.exe scripts\export_interaction_stats.py `
  --db data\analytics\linehelper_interactions.db `
  --days 30 `
  --output-json data\analytics\exports\last_30_days.json `
  --output-csv-dir data\analytics\exports\last_30_days
```

Экспорт включает totals и feedback rates, distributions по answer mode, response kind, fact type и intent, причины negative feedback, error/timeout counts, median/p95 duration, insufficient/partial/no-answer-with-evidence counters, top rendered sources и top negative questions при разрешённом хранении текста. Отсутствие negative feedback не трактуется как доказательство правильности ответа.

## Тесты

Test budget соблюдён:

- 8 unit items: schema idempotency, interaction/source persistence, append-only feedback, retention cascade, disabled logger, sanitizer, statistics, whitelist serialization и deterministic source ordering;
- 3 integration items: successful answer logging, logger exception isolation, UI positive/negative feedback flow.

Результаты:

- analytics unit/integration: `11 passed in 0.52s` после финального SQLite fix;
- объединённые targeted answer/UI/conversation/analytics tests: `64 passed in 1.02s`;
- `compileall linehelper tests scripts`: успешно;
- полный pytest: `512 passed in 19.43s` (предыдущая точка — 501, добавлено ровно 11 test items).

## Deterministic smoke

Smoke без Ollama на временной SQLite DB подтвердил:

- записан один interaction;
- записаны два append-only feedback events: positive и negative;
- JSON export создан;
- retention удалил один expired interaction вместе со связанными данными;
- временная DB и каталог успешно удалены.

Итог smoke: `smoke_interactions=1`, `smoke_feedback_events=2`, `smoke_json_export=True`, `smoke_cleanup_deleted=1`, `smoke_temp_removed=True`.

## Ограничения

- нет аналитического web-dashboard;
- feedback субъективен и необязателен;
- нет автоматического преобразования плохого ответа в regression case;
- sanitizer не является полной DLP-системой;
- нет синхронизации между компьютерами;
- SQLite рассчитана на локальный MVP;
- interaction logs не используются для автоматического обучения;
- latency-поля, отсутствующие в текущем pipeline, сохраняются как `NULL`.

## Границы проверки

RAG semantics и существующие answer fields не менялись. Новых architecture fixture cases нет. Полный 71-case architecture pack, organization pack, repeatability и Ollama live-run не запускались, поскольку итерация добавляет изолированный эксплуатационный side effect.

Commit и push не выполнялись. Ранее существовавшие untracked-отчёты архитектурных этапов 9 и 11 не изменялись и не включались в scope итерации.
