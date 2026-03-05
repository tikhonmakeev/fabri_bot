# Contributing Guidelines 🤝

Спасибо за интерес к Medical Intake Bot! Мы рады видеть ваш вклад.

## Как помочь проекту

### 1. Найдите или создайте Issue

- Проверьте [Issues](https://github.com/YOUR_USERNAME/medical-intake-bot/issues)
- Если проблемы нет — создайте новую с описанием
- Обсудите перед началом работы на сложных задачах

### 2. Fork репозитория

```bash
# На странице репозитория нажмите "Fork"
```

### 3. Создайте branch для вашей фичи

```bash
git clone https://github.com/YOUR_USERNAME/medical-intake-bot.git
cd medical-intake-bot
git checkout -b feature/описание-фичи
```

**Правила для имен branches:**
- `feature/` — новая функциональность
- `fix/` — исправление багов
- `docs/` — документация
- `refactor/` — улучшение кода
- `test/` — тесты

### 4. Внесите изменения

- Следуйте стилю кода проекта (PEP 8)
- Добавляйте комментарии для сложной логики
- Обновляйте README при необходимости
- Тестируйте изменения

### 5. Commit с хорошими сообщениями

```bash
# Примеры хороших сообщений
git commit -m "feat: add pain_triggers conditional logic"
git commit -m "fix: handle chat_not_found error gracefully"
git commit -m "docs: update README with scoring explanation"
git commit -m "refactor: simplify validator functions"

# Формат
# [type]: [brief description]
# 
# Optional detailed explanation
```

**Типы:**
- `feat` — новая функция
- `fix` — исправление
- `docs` — документация
- `refactor` — рефакторинг
- `test` — тесты
- `chore` — конфигурация

### 6. Push и создайте Pull Request

```bash
git push origin feature/описание-фичи
```

На GitHub:
1. Нажмите "Compare & pull request"
2. Заполните описание PR
3. Ожидайте review

## Требования к коду

### Python стиль (PEP 8)

```python
# ✅ Хорошо
def calculate_score(answers: dict[str, Any]) -> int:
    """Calculate Fabry risk score."""
    score = 0
    if answers.get("fabry_confirmed") == "Да":
        score += 10
    return score

# ❌ Плохо
def calcScore(answers):
    s=0
    if answers.get("fabry_confirmed")=="Да":s+=10
    return s
```

### Типизация

Используйте type hints:
```python
def process_data(data: dict[str, Any]) -> str:
    pass
```

### Тестирование

При добавлении функции добавляйте тесты (если применимо).

## Локальное тестирование

```bash
# Установите зависимости для разработки
pip install -r requirements.txt

# Запустите бота локально
python main.py

# Проверьте синтаксис
python -m py_compile main.py
```

## Docker тестирование

```bash
# Соберите image
docker build -t medical-bot-test .

# Запустите
docker run -d \
  --name test-bot \
  --env-file .env.example \
  medical-bot-test

# Проверьте логи
docker logs -f test-bot

# Остановите
docker stop test-bot
```

## Документация

- Обновляйте README при добавлении функций
- Документируйте функции docstrings
- Добавляйте комментарии для некоторого окода

Пример:
```python
def _should_ask_pain_triggers(data: dict[str, Any]) -> bool:
    """
    Determine if pain triggers question should be asked.
    
    Only ask about Fabri crisis triggers if user reported pain
    (not "Никогда" response to previous question).
    
    Args:
        data: FSM context data with user answers
        
    Returns:
        True if pain_hands_feet answer is not "Никогда"
    """
    pain = data.get("pain_hands_feet")
    return pain is not None and pain != "Никогда"
```

## Проверка перед Submit

- [ ] Код следует PEP 8
- [ ] Есть type hints
- [ ] Функции документированы
- [ ] .env НЕ добавлен в коммит
- [ ] README обновлен (если нужно)
- [ ] Сообщение коммита понятное
- [ ] Тестировано локально
- [ ] Нет больших файлов (< 100MB)

## Report а баг

Создайте Issue с:

```markdown
## Описание проблемы
[Что не работает]

## Шаги воспроизведения
1. 
2. 
3. 

## Ожидаемое поведение
[Что должно быть]

## Фактическое поведение
[Что происходит]

## Окружение
- Python версия: 3.11
- ОС: Windows/Mac/Linux
- aiogram версия: 3.15.0

## Логи
```
[Вставьте релевантные логи]
```

## Предложить фичу

```markdown
## Описание
[Краткое описание]

## Зачем это нужно
[Проблема которую это решает]

## Предложенное решение
[Как это должно работать]

## Альтернативы
[Другие варианты]
```

## Правила поведения

- Будьте вежливы и уважительны
- Критикуйте код, а не людей
- Помогайте новичкам
- Следуйте [Contributor Covenant](https://www.contributor-covenant.org/)

## Questions?

Создайте Issue с тегом `question` или напишите в дискуссии.

---

**Спасибо за вклад!** 🙏
