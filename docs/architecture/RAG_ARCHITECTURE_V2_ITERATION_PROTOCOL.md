# Протокол архитектурных итераций RAG v2

## Назначение

Этот документ задаёт обязательный воспроизводимый цикл для каждой архитектурной
итерации RAG-контура LineHelper. Итерация оценивается не по одному удачному
ответу, а по поведению слоя, соседним сценариям и архитектурным инвариантам.

## Обязательный цикл

```text
baseline
→ ограниченное изменение одного слоя
→ unit tests
→ deterministic integration tests
→ live runtime probe
→ architectural regression pack
→ full pytest
→ сравнение до/после
→ отдельный Markdown-отчёт
→ ручное подтверждение
→ commit
```

Commit создаётся только после ручного подтверждения. Codex не коммитит изменения
автоматически.

## 1. Baseline

До изменения фиксируются:

- ветка, исходный commit и `git status --short`;
- рабочая модель, analyzer model, Python, ОС, путь к memory DB;
- полный pytest;
- архитектурный regression pack;
- live probe либо честный статус `blocked`;
- метрики и недоступная observability;
- известные ограничения и риски.

Baseline должен быть машиночитаемым и иметь стабильное Markdown-резюме. Runtime
outputs сохраняются в `data/test_runs/`, если этот каталог исключён из Git.

## 2. Ограничение изменения

Одна итерация меняет один архитектурный слой и заранее перечисляет:

- файлы и ответственность слоя;
- разрешённые изменения;
- запрещённые соседние изменения;
- ожидаемые инварианты;
- риски и сценарии возможной регрессии.

Нельзя писать специальные ответы, словари фамилий или исключения под точные
тексты вопросов. Исправляется класс ошибки, а не отдельная формулировка.

## 3. Автоматические тесты

### Unit tests

Unit tests полностью детерминированы:

- используют fake/mock-компоненты;
- не запускают реальную Ollama;
- проверяют contracts, classification, метрики и сериализацию;
- не считают дословный final text главным критерием;
- не маскируют архитектурные цели бессрочным `xfail`.

### Deterministic integration tests

Используются реальный orchestration-код и test-only зависимости:

- fake Query Analyzer;
- fake retriever или тестовая SQLite DB;
- fake LLM;
- production entrypoint, когда это возможно.

Проверяются вызовы retrieval, сохранение evidence, clarification boundary,
semantic/operational boundary, context inclusion и запрет неподтверждённых
людей или подразделений.

### Live runtime probe

Live probe запускает тот же основной pipeline, что UI:

```text
RagAnswerGenerator
→ Query Analyzer
→ validation
→ retrieval
→ rerank
→ context selection
→ evidence gate
→ answer
```

Runner не дублирует production RAG-логику. Прозрачные test-only wrappers могут
записывать входы и выходы production-компонентов.

Live LLM-тесты не входят в обычный unit pytest. Ошибка или недоступность Ollama
помечает live-сценарий как `blocked`, а не как дефект unit-тестов.

## 4. Два контура оценки

### Обычный pytest

Обычный pytest должен оставаться зелёным и проверять:

- test harness;
- стабильные функции production-кода;
- deterministic integration;
- отсутствие регрессий уже работающего поведения.

Известная архитектурная ошибка не добавляется как заведомо падающий pytest.

### Architecture target pack

Target pack использует статусы:

- `passed`;
- `failed`;
- `blocked`;
- `not_applicable`.

Текущие архитектурные дефекты отображаются как `failed` в baseline-отчёте. После
устранения конкретной причины её инварианты переводятся в обязательные
deterministic regression tests.

## 5. Обязательные варианты класса ошибки

Каждый исправленный класс ошибки должен иметь:

- прямой вопрос;
- разговорный вариант;
- перефразирование;
- отрицательный пример;
- соседний операционный пример.

Все обнаруженные регрессии добавляются в test pack. Сценарии проверяют различие
способностей и типов факта, а не совпадение ключевых слов.

## 6. Архитектурные инварианты

По возможности проверяются:

- `resolved_question`;
- `QueryPlan`;
- intent;
- тип запрашиваемого факта;
- clarification decision и неоднозначный фрагмент;
- semantic/operational boundary;
- retrieval queries;
- raw и merged candidates;
- обязательные chunks;
- chunks в context;
- evidence decision и coverage subclaims;
- итоговый режим ответа;
- источники;
- неподтверждённые утверждения;
- повторяемость ключевых решений.

Если production runtime не отдаёт поле, runner сохраняет:

```json
{
  "available": false,
  "reason": "not implemented in current architecture"
}
```

Неизвестная метрика не подменяется нулём: используется `not_available`.

## 7. Соседние сценарии

Минимально проверяются:

- исходный сценарий;
- варианты того же класса;
- отрицательный пример;
- соседний предметный сценарий;
- парный semantic/operational сценарий;
- multi-turn и смена темы, если слой использует history.

Нельзя завершать итерацию только потому, что исправился исходный пользовательский
вопрос.

## 8. Обязательные запуски

Используется рабочая `.venv`.

```powershell
.\.venv\Scripts\python.exe -m compileall linehelper tests scripts

.\.venv\Scripts\python.exe -m pytest tests scripts\tests `
  --basetemp .\.venv\pytest-tmp-rag-v2-<unique-id> `
  -p no:cacheprovider

.\.venv\Scripts\python.exe scripts\run_rag_architecture_v2_baseline.py
```

При блокировке pytest-каталога на Windows используется новый уникальный
`--basetemp`; заблокированный каталог не удаляется автоматически.

Дополнительно запускается релевантный существующий regression runner, например
organization pack в retrieval-only или коротком целевом режиме.

## 9. Сравнение до/после

Сравнение содержит:

- исходный и итоговый commit/tree;
- перечень production-файлов в diff;
- одинаковые параметры runtime;
- результаты полного pytest;
- target-pack statuses;
- метрики retrieval, context, evidence, clarification и operational boundary;
- repeatability;
- новые, устранённые и неизменившиеся failures;
- изменение observability.

Если production-код не менялся, это явно подтверждается `git diff -- linehelper`
и сравнением production tree hash.

## 10. Отчёт итерации

Каждая итерация формирует отдельный Markdown-отчёт со следующими разделами:

1. Git и runtime.
2. Scope слоя.
3. Baseline.
4. Реализованное изменение.
5. Новые и обновлённые тесты.
6. Full pytest.
7. Architecture target pack.
8. Live probe.
9. Сравнение до/после.
10. Недоступная observability.
11. Известные ограничения.
12. Риски.
13. Условия перехода к следующей итерации.
14. Предлагаемый commit message.

Полные model outputs, SQLite DB, логи Ollama, `.venv` и временные pytest-каталоги
не коммитятся.

## 11. Ручное подтверждение и commit

До commit пользователь проверяет:

- ограниченность diff;
- отсутствие специальных ответов под вопросы;
- зелёный pytest;
- полный target report, включая failures;
- воспроизводимость команд;
- ограничения и риски;
- предлагаемый commit message.

Только после этого создаётся commit отдельной командой пользователя или по его
явному поручению.

## 12. Definition of Done

Итерация завершена, когда:

- изменён только заявленный слой;
- добавлены или обновлены автоматические тесты;
- проверены соседние сценарии;
- прошёл полный pytest;
- выполнен architecture target pack;
- live probe выполнен или честно `blocked`;
- сформированы метрики без ложных нулей;
- сравнение до/после сохранено;
- создан отдельный Markdown-отчёт;
- перечислены ограничения и риски;
- предложен commit message;
- получено ручное подтверждение перед commit.
