# LineHelper: памятка по запуску и управлению

## 1. Что такое команда linehelper

`linehelper` - единая команда управления программой LineHelper.

Через нее можно:

- запустить интерфейс LineHelper в браузере;
- проверить, все ли готово для работы;
- задать вопрос прямо из терминала;
- обновить индекс оргструктуры;
- запустить тесты;
- запустить приложение в фоне и потом остановить его.

Если запустить команду без дополнительных слов, она просто откроет основной интерфейс:

```powershell
linehelper
```

Это то же самое, что:

```powershell
linehelper start
```

## 2. Первоначальная установка команды

Откройте Windows PowerShell и выполните:

```powershell
cd "C:\Users\Nikolai Paliy\work\projects\linehelper"
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install -e .
```

После этого в активированной среде должна появиться команда:

```powershell
linehelper --help
```

Если PowerShell не дает активировать окружение, запустите один раз:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Затем закройте PowerShell, откройте заново и повторите установку.

## 3. Запуск интерфейса

Обычный запуск:

```powershell
linehelper
```

или:

```powershell
linehelper start
```

LineHelper запустит веб-интерфейс. Остановить его можно сочетанием `Ctrl+C` в том же окне PowerShell.

Запуск на другом порту:

```powershell
linehelper start --port 8502
```

Запуск без автоматического открытия браузера:

```powershell
linehelper start --no-browser
```

## 4. Запуск в фоне

Фоновый запуск удобен, если вы хотите закрыть окно запуска или продолжить работать в терминале:

```powershell
linehelper start --background
```

Команда покажет:

- номер процесса;
- адрес интерфейса;
- путь к журналу работы.

Обычно адрес выглядит так:

```text
http://localhost:8501
```

## 5. Остановка фонового LineHelper

Чтобы остановить приложение, запущенное через `--background`:

```powershell
linehelper stop
```

Если LineHelper в фоне не запущен, команда спокойно сообщит об этом и не покажет traceback.

## 6. Проверка состояния

Команда:

```powershell
linehelper status
```

показывает:

- запущен ли LineHelper в фоне;
- PID процесса;
- адрес интерфейса;
- путь к базе памяти;
- есть ли база;
- сколько найдено semantic, episodic и organization chunks;
- доступна ли Ollama;
- какая модель настроена;
- доступна ли эта модель;
- состояние Query Analyzer.

Эта команда ничего не меняет в базе.

## 7. Диагностика

Команда:

```powershell
linehelper doctor
```

проверяет окружение LineHelper и выводит строки вида:

```text
[OK] Python 3.x
[OK] Streamlit установлен
[OK] Ollama доступна
[FAIL] Модель qwen2.5:14b не найдена в Ollama
```

Что означают статусы:

- `[OK]` - все хорошо;
- `[WARN]` - есть предупреждение, но работа может продолжаться;
- `[FAIL]` - есть проблема, которую нужно исправить.

## 8. Вопрос из терминала

Можно задать один вопрос без открытия интерфейса:

```powershell
linehelper chat "Кто главный у закупщиков?"
```

LineHelper выведет вопрос, ответ и источники.

Для подробной диагностики:

```powershell
linehelper chat --debug "Кто главный у закупщиков?"
```

Debug-режим показывает служебные данные поиска: intent, нормализованный вопрос, расширения запроса, найденные chunks и время выполнения.

## 9. Обновление оргструктуры

Чтобы переиндексировать оргструктуру:

```powershell
linehelper index organization
```

Пробный запуск без записи в базу:

```powershell
linehelper index organization --dry-run
```

Подробный вывод:

```powershell
linehelper index organization --verbose
```

## 10. Тесты

Тесты оргструктуры:

```powershell
linehelper test organization
```

Примеры:

```powershell
linehelper test organization --limit 5
linehelper test organization --group ORG07
linehelper test organization --retrieval-only
```

Тесты runtime Query Analyzer:

```powershell
linehelper test runtime
```

Примеры:

```powershell
linehelper test runtime --limit 5
linehelper test runtime --group G01
linehelper test runtime --debug
```

## 11. Версия

Команда:

```powershell
linehelper version
```

показывает версию LineHelper, текущий git commit, состояние рабочей копии и версию Python.

## 12. Краткая шпаргалка

```powershell
linehelper
linehelper start
linehelper start --background
linehelper stop
linehelper status
linehelper doctor
linehelper chat "Кто главный у закупщиков?"
linehelper chat --debug "Кто главный у закупщиков?"
linehelper index organization
linehelper index organization --dry-run
linehelper test organization --limit 5
linehelper test runtime --limit 5
linehelper version
linehelper --help
```
