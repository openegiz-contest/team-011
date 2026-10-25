# Карьер NETA: цифровой двойник на OpenEgiz

Командный репозиторий [team-011](https://github.com/openegiz-contest/team-011) конкурса «Цифровой рудник на OpenEgiz».

Живой стенд — открытый медный карьер с тремя модулями (диспетчеризация, негабарит, допуск после взрыва), режимами А/Б и сценариями «что если». Описание решения: [SUBMISSION.md](SUBMISSION.md). Презентация: [presentation.pdf](presentation.pdf).

## Запуск

Требования (Docker, `make`, Windows/WSL2): [OPENEGIZ.md](OPENEGIZ.md) / [OPENEGIZ.ru.md](OPENEGIZ.ru.md).

На чистой Ubuntu 24.04:

```bash
sudo apt install -y make docker.io docker-compose-v2   # если ещё нет
sudo usermod -aG docker "$USER" && newgrp docker       # один раз
git clone https://github.com/openegiz-contest/team-011.git
cd team-011
make pit
```

`make pit` поднимает платформу OpenEgiz и карьер NETA (двойники, симулятор, сервисы модулей, UI). Первый запуск ~2–5 минут (скачивание образов и сборка).

Остановка: `make down`. Полная очистка данных: `make clean`.

Пакетный замер А/Б (после хотя бы одного `make pit`, образ уже собран):

```bash
make pit-experiment SHIFTS=30
```

## Что открыть

После `make pit` скрипт печати адресов покажет URL; по умолчанию:

| Что | Адрес |
|---|---|
| **Карта карьера (2D/3D), режимы А/Б, сценарии** | http://127.0.0.1:8095/ |
| Eclipse Ditto (двойники `neta.pit:*`) | http://127.0.0.1:8080/ |
| Grafana → дашборд «Карьер NETA — модули 1–3» | http://127.0.0.1:3000/ (логин из вывода `make pit` / `.env`) |

На вкладке «Управление»: переключатели модулей А/Б, ускорение смены, «Перезапустить смену», сценарии «что если». На вкладке «Показатели»: тоннаж, диспетчер, дробилка, взрыв, завершённые смены.

## Содержимое репозитория

| Путь | Содержимое |
|---|---|
| `mine/` | Карьер NETA: симулятор, сервисы, UI, Grafana |
| `SUBMISSION.md` | Описание решения (**обязательно**) |
| `presentation.pdf` | Презентация (**обязательно**) |
| `examples/mine/` | Пример платформы (не база нашей сдачи) |
| `OPENEGIZ.md`, `OPENEGIZ.ru.md` | Документация OpenEgiz v1.0.4 |
| `AGENTS.md` | Инструкции для AI-агентов |

## Лицензия

MIT, см. [LICENSE](LICENSE).

---

Platform: [OpenEgiz](https://github.com/aleka07/openegiz) v1.0.4.
