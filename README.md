# Funding Arb Bot

Cross-exchange funding rate arbitrage бот для Bybit + Hyperliquid.

**Стратегия:** дельта-нейтральная — открываем SHORT на бирже с высоким funding rate и LONG на бирже с низким (или отрицательным) funding rate. Зарабатываем разницу funding'а каждые 8 часов. Цена монеты не имеет значения (хеджированы).

---

## Быстрый старт

```bash
git clone <repo>
cd funding-arb-bot

python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Заполни TELEGRAM_TOKEN и TELEGRAM_CHAT_ID (опционально, но рекомендуется)

# Запуск сканера (только смотрим возможности, не торгуем):
python scripts/scan_only.py

# Запуск бота в paper режиме (симуляция, без реальных денег):
python main.py
```

---

## Как это работает

```
Bybit funding rate:       +0.05% за 8 часов
Hyperliquid funding rate: -0.01% за 8 часов
Разница: 0.06% за 8 часов = ~65% годовых

Бот делает:
  Bybit:        открывает SHORT  → получает +0.05% × размер
  Hyperliquid:  открывает LONG   → получает +0.01% × размер
  (Лонг получает positive funding когда rate отрицательный)

Размер позиций одинаковый = дельта 0 → цена монеты неважна
```

---

## Структура

```
funding-arb-bot/
├── main.py                       # Главный цикл
├── config.py                     # Загрузка .env
├── scripts/
│   └── scan_only.py              # Только сканирование
├── src/
│   ├── exchanges/                # Адаптеры Bybit + Hyperliquid
│   ├── scanner/                  # Поиск возможностей
│   ├── strategy/                 # Risk manager
│   ├── execution/                # Paper trader
│   ├── monitoring/               # Funding monitor
│   ├── database/                 # SQLite модели
│   └── notifications/            # Telegram
└── .env                          # Секреты (НЕ коммитим)
```

---

## Режимы запуска

В `.env` параметр `MODE`:

| Mode | Что делает | Когда использовать |
|------|------------|--------------------|
| `paper` | Симулирует сделки, ничего не размещает | **Старт. Минимум 2 недели для проверки** |
| `testnet` | Реальные ордера на testnet биржах | После paper trading с положительным PnL |
| `live` | Реальные ордера на mainnet | Только после testnet с малым капиталом |

---

## Ключевые параметры (`.env`)

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `CAPITAL_USD` | 1000 | Виртуальный/реальный капитал |
| `MIN_FUNDING_DIFF_PCT` | 0.03 | Минимальная разница funding для входа |
| `MAX_POSITIONS` | 3 | Макс одновременных позиций |
| `MAX_POSITION_PCT` | 30 | Макс размер одной позиции от капитала |
| `STOP_LOSS_PCT` | 2 | Stop-loss на позицию |
| `DAILY_DRAWDOWN_LIMIT_PCT` | 5 | Лимит дневного убытка |

---

## Логика входа

Бот открывает позицию когда:
- Разница funding rates > `MIN_FUNDING_DIFF_PCT` (нормализовано к 8h)
- Объём 24h на обеих биржах > `MIN_VOLUME_24H_USD`
- Basis (расхождение цен) < 0.5%
- Не превышены лимиты RiskManager

## Логика выхода

Позиция закрывается если:
- Funding rate перевернулся (стал отрицательным для нашей стороны)
- Funding diff упал ниже порога
- Basis расширился > 0.5%
- Сработал stop-loss
- Превышен дневной drawdown

---

## Безопасность

**Что НЕ коммитить в git:**
- `.env` — API ключи
- `*.db` — история сделок
- `logs/` — логи могут содержать чувствительные данные

**API permissions для live режима:**
- Bybit: только Read + Trade (БЕЗ Withdraw!)
- Hyperliquid: subaccount с ограниченным капиталом

---

## Проверка перед переходом на live

1. **Минимум 2 недели paper trading** с положительным итоговым PnL
2. Win rate > 70%
3. Нет критических багов
4. Telegram уведомления работают надёжно
5. Тест на testnet 3-7 дней
6. Запуск с $100 на 1 неделю
7. Только потом — целевой капитал

---

## Disclaimer

Это образовательный проект. Торговля криптовалютой связана с риском потери средств. Автор не несёт ответственности за ваши убытки. Используй на свой страх и риск.
