-- Сколько записей загрузилось
SELECT COUNT(*) FROM grader_attempts;

-- Количество запусков и отправок решений
SELECT attempt_type, COUNT(*)
FROM grader_attempts
GROUP BY attempt_type;

-- Сколько успешных решений было у каждого пользователя
SELECT user_id, COUNT(*) AS correct_answers
FROM grader_attempts
WHERE attempt_type = 'submit' AND is_correct = TRUE
GROUP BY user_id
ORDER BY correct_answers DESC;
