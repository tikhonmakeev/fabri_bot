# 🚀 Инструкция по загрузке проекта на GitHub

## Шаг 1: Подготовка (один раз)

### 1.1 Установите Git
Скачайте и установите с https://git-scm.com/

### 1.2 Настройте Git
```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

### 1.3 Создайте SSH ключ (опционально, но рекомендуется)
```bash
ssh-keygen -t ed25519 -C "your.email@example.com"
```
Скопируйте публичный ключ (~/.ssh/id_ed25519.pub) в GitHub Settings → SSH keys

## Шаг 2: Создайте репозиторий на GitHub

1.去 https://github.com/new
2. Заполните:
   - **Repository name**: `medical-intake-bot` (или любое другое имя)
   - **Description**: `Telegram bot for primary medical questionnaire on Fabry disease`
   - **Visibility**: Public (или Private если нужно)
   - **Добавьте лицензию**: MIT (уже в проекте)
3. **НЕ** инициализируйте с README, .gitignore или лицензией (они уже есть)
4. Нажмите "Create repository"

## Шаг 3: Инициализируйте локальный репозиторий

```bash
cd c:\bottt

# Инициализируйте Git
git init

# Добавьте все файлы (кроме исключенных в .gitignore)
git add .

# Проверьте что добавилось (не должно быть .env)
git status

# Создайте первый коммит
git commit -m "Initial commit: Medical Intake Bot for Fabry disease screening"
```

## Шаг 4: Подключитесь к GitHub

Скопируйте команду из GitHub (выглядит примерно так):

**С HTTPS:**
```bash
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/medical-intake-bot.git
git push -u origin main
```

**С SSH (если настроили ключи):**
```bash
git branch -M main
git remote add origin git@github.com:YOUR_USERNAME/medical-intake-bot.git
git push -u origin main
```

Замените `YOUR_USERNAME` на ваше имя на GitHub.

## Шаг 5: Проверьте результат

- Зайдите на https://github.com/YOUR_USERNAME/medical-intake-bot
- Убедитесь что файлы загрузились
- **Проверьте что .env НЕ загружен** (он должен быть в .gitignore)

## Дальнейшие обновления

После первоначальной загрузки для добавления изменений:

```bash
# Внесите изменения в файлы

# Добавьте изменения
git add .

# Создайте коммит
git commit -m "Описание изменений"

# Загрузите на GitHub
git push origin main
```

## Полезные команды

```bash
# Просмотреть статус
git status

# Просмотреть историю коммитов
git log --oneline

# Просмотреть различия
git diff

# Отменить последний коммит (осторожно!)
git reset --soft HEAD~1

# Проверить что находится в staging area
git diff --cached
```

## Безопасность

✅ **Проверьте перед первой загрузкой:**
- [ ] .env файл в .gitignore
- [ ] .env файл НЕ добавлен в git
- [ ] BOT_TOKEN НЕ виден в коде
- [ ] Нет других чувствительных данных

```bash
# Убедитесь что .env не отслеживается
git ls-files | grep .env
# Не должно вывести ничего
```

## Если что-то пошло не так

### Случайно добавили .env в git?
```bash
# Удалите из истории (опасно!)
git rm --cached .env
git commit --amend --no-edit

# Переиспишите ветку (если еще не pushed)
git push --force-with-lease origin main
```

### Забыли добавить файлы?
```bash
git add file_name.txt
git commit --amend --no-edit
git push --force-with-lease origin main
```

## Следующие шаги

После загрузки на GitHub можно:
1. Добавить **Topics** (fabry-disease, telegram-bot, medical)
2. Включить **GitHub Pages** в Settings → Pages
3. Настроить **GitHub Actions** для CI/CD
4. Добавить **Issues** шаблоны в `.github/ISSUE_TEMPLATE/`
5. Пригласить коллаб в Settings → Collaborators

## Документация

- [GitHub Documentation](https://docs.github.com/en/github)
- [Git Tutorial](https://www.atlassian.com/git/tutorials)
- [Pro Git Book](https://git-scm.com/book/en/v2)
