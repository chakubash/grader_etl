# Итоговый проект по Python Д. Иванова-Чакубаша

Скрипт получает данные о попытках студентов из API ITResume, проверяет их и сохраняет в PostgreSQL. После этого считает статистику по дням и записывает её в Google Sheets. 

## Для запуска надо  

1. Установить библиотеки: `pip install -r requirements.txt`.
2. Запустить PostgreSQL: `docker compose up -d`.
3. Для Google Sheets включить Sheets API и Drive API в Google Cloud, создать Service Account и сохранить JSON-ключ как `google_credentials.json`. Дать email сервисного аккаунта доступ к таблице как редактору. ID таблицы добавить в `.env`.
4. Запустить `python main.py`.

## Результаты

Проект проверен на данных за 31 мая 2023 года.
Из API получено 6473 записи, в PostgreSQL загружено 6458.
Ещё 15 записей пропущены при проверке данных.

Рассчитанная дневная статистика выгружена в Google Sheets - https://docs.google.com/spreadsheets/d/1OZyXWozDVcjcNyzcLsw5Ab94TYhBXYVACsY1Wie0q60/edit?usp=sharing 
