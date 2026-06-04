# План впровадження: Авторизація користувачів (Supabase) та контейнеризація для Hostinger

Цей план детально описує процес впровадження системи реєстрації та входу користувачів на базі хмарної бази даних **Supabase**, перенесення віртуального гаманця гри «Біржа» з локального сховища (`localStorage`) у безпечну базу даних PostgreSQL, а також контейнеризацію проекту за допомогою **Docker** для деплою на **Hostinger** під доменом **btc.lexis.blog**.

---

## 1. Схема бази даних Supabase (PostgreSQL)

Всі таблиці створюються в панелі керування Supabase через SQL Editor. 

### Таблиця `wallets` (Віртуальні гаманці користувачів)
Зберігає поточний баланс користувачів у USD та BTC, а також середню ціну входу для активної позиції.
```sql
CREATE TABLE public.wallets (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    usd_balance NUMERIC(20, 8) NOT NULL DEFAULT 100.00000000 CHECK (usd_balance >= 0),
    btc_balance NUMERIC(20, 8) NOT NULL DEFAULT 0.00000000 CHECK (btc_balance >= 0),
    avg_buy_price NUMERIC(20, 8) NOT NULL DEFAULT 0.00000000 CHECK (avg_buy_price >= 0),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Таблиця `trades` (Журнал операцій)
Зберігає повну історію купівлі та продажу біткоїнів кожним користувачем.
```sql
CREATE TABLE public.trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type VARCHAR(10) NOT NULL CHECK (type IN ('buy', 'sell')),
    btc_amount NUMERIC(20, 8) NOT NULL CHECK (btc_amount > 0),
    price NUMERIC(20, 8) NOT NULL CHECK (price > 0),
    fee NUMERIC(20, 8) NOT NULL CHECK (fee >= 0),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Створення індексу для швидкого пошуку історії угод користувача
CREATE INDEX idx_trades_user_id ON public.trades(user_id);
```

### Тригер для автоматичного створення гаманця
При реєстрації нового користувача в Supabase Auth йому автоматично створюється гаманець із балансом $100.00.
```sql
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.wallets (user_id, usd_balance, btc_balance, avg_buy_price)
    VALUES (NEW.id, 100.00000000, 0.00000000, 0.00000000);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

---

## 2. Бекенд-інтеграція (Flask / Python)

### Нові залежності
У [requirements.txt](file:///Users/kostantinkrivula/Desktop/Kronos-master/requirements.txt) та `webui/requirements.txt` додаються:
* `supabase>=2.0.0` (клієнт для роботи з Supabase)
* `python-dotenv>=1.0.0` (для безпечного читання ключів з файлу `.env`)

### Налаштування середовища (`.env`)
Створюється локальний файл конфігурації (який ігнорується Git):
```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-service-role-key-or-anon-key
FLASK_SECRET_KEY=super-secret-session-key
```

### Зміни у [webui/app.py](file:///Users/kostantinkrivula/Desktop/Kronos-master/webui/app.py)
1. **Ініціалізація клієнта Supabase** на старті сервера.
2. **Сесії Flask**: збереження авторизаційного токена та ID користувача в `flask.session`.
3. **Ендпоінти авторизації:**
   * `/api/auth/register` (POST) — реєстрація через Supabase Auth.
   * `/api/auth/login` (POST) — авторизація користувача, збереження JWT в сесії.
   * `/api/auth/logout` (POST) — вихід, очищення сесії.
   * `/api/auth/status` (GET) — перевірка, чи авторизований користувач, повернення email.
4. **Ендпоінти гри «Біржа»:**
   * `/api/exchange/wallet` (GET) — отримання балансу USD/BTC та середньої ціни з таблиці `wallets`.
   * `/api/exchange/trade` (POST) — виконання ордерів купівлі/продажу:
     - Перевірка лімітів балансу в БД.
     - Розрахунок комісії 0.1%.
     - Оновлення балансу гаманця та запис операції в таблицю `trades` (в рамках однієї транзакції).
     - Повернення нового стану.
   * `/api/exchange/reset` (POST) — скидання балансу гаманця до $100.00 та очищення історії транзакцій.

---

## 3. Фронтенд-інтеграція (HTML/JS)

### Зміни у [index.html](file:///Users/kostantinkrivula/Desktop/Kronos-master/webui/templates/index.html)
1. **Інтерфейс авторизації:**
   * Додавання нового пункту меню на лівій панелі: `🔐 Кабінет` або `Вхід/Реєстрація`.
   * Створення модального вікна / екрану для реєстрації та входу (гарний дизайн у колірній гамі сайту).
2. **Прив'язка інтерфейсу до стану входу:**
   * Якщо користувач не увійшов — вкладка «🏦 Біржа» заблокована (або показує повідомлення: *«Будь ласка, увійдіть, щоб грати»*).
   * Якщо увійшов — відображається його email, кнопка «Вийти» та активується гра.
3. **Асинхронний зв'язок з БД:**
   * Функції `loadWallet()`, `executeTrade()` та `resetWallet()` тепер роблять `fetch()` запити на серверні ендпоінти Flask (`/api/exchange/...`), а не працюють з локальним `localStorage`.
   * При вході користувача дані автоматично підтягуються з його хмарного профілю.

---

## 4. Контейнеризація проекту (Docker)

Для деплою на Hostinger створюється [NEW] `Dockerfile` у корені проекту.

```dockerfile
# Stage 1: Build dependencies and download PyTorch (if CPU version)
FROM python:3.10-slim as builder

WORKDIR /app

# Встановлюємо інструменти для компіляції (якщо якісь бібліотеки цього потребують)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime image
FROM python:3.10-slim

WORKDIR /app

COPY --from=builder /root/.local /root/.local
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Відкриваємо порт Flask
EXPOSE 7070

# Переходимо у папку webui для запуску
WORKDIR /app/webui
CMD ["python", "run.py"]
```

---

## 5. Налаштування на Hostinger під доменом `btc.lexis.blog`

Hostinger надає можливість деплою Docker-контейнерів на VPS. Для маршрутизації домену та захисту SSL (HTTPS) найкраще використати **Nginx Reverse Proxy** на VPS.

### Крок 1. Налаштування DNS
В панелі керування вашим доменом `lexis.blog` (наприклад, Cloudflare, Hostinger DNS тощо) потрібно створити новий запис:
* **Тип:** `A`
* **Ім'я (Host):** `btc`
* **Знавно (Points to):** `<IP-адреса_вашого_VPS_на_Hostinger>`

### Крок 2. Збірка та запуск контейнера на VPS
На Hostinger VPS виконуються команди:
```bash
# Клонування репозиторію
git clone https://github.com/YAROVISION/exchange.git
cd exchange

# Створення файлу з ключами Supabase
echo "SUPABASE_URL=https://your-project.id.supabase.co" > webui/.env
echo "SUPABASE_KEY=your-secret-key" >> webui/.env
echo "FLASK_SECRET_KEY=$(openssl rand -hex 24)" >> webui/.env

# Збірка Docker-образу
docker build -t exchange-app .

# Запуск контейнера (мапимо порт 7070 контейнера на локальний 7070 сервера)
docker run -d --name exchange-web -p 7070:7070 --restart unless-stopped exchange-app
```

### Крок 3. Налаштування Nginx (Reverse Proxy) на Hostinger VPS
Встановлюємо Nginx на VPS для проксування запитів з порту 80/443 на порт 7070:
```bash
sudo apt update
sudo apt install nginx -y
```

Створюємо файл конфігурації `/etc/nginx/sites-available/btc.lexis.blog`:
```nginx
server {
    listen 80;
    server_name btc.lexis.blog;

    location / {
        proxy_pass http://127.0.0.1:7070;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded-for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активуємо конфігурацію та перезапускаємо Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/btc.lexis.blog /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Крок 4. Налаштування безкоштовного SSL-сертифікату (HTTPS)
Використовуємо Let's Encrypt Certbot для отримання безкоштовного HTTPS-сертифікату:
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d btc.lexis.blog
```
Certbot автоматично оновить конфігурацію Nginx, налаштує редирект з HTTP на HTTPS та встановити автооновлення сертифікату кожні 90 днів.

---

## 6. План тестування та верифікації

### Локальне тестування
1. Перевірка локального запуску Docker-контейнера:
   `docker run -p 7070:7070 exchange-app`
2. Перевірка реєстрації та створення нового гаманця у Supabase Auth та таблиці `wallets`.
3. Симуляція торгівлі: перевірка, що баланси оновлюються в БД, а угоди логуються в таблиці `trades`.
4. Перевірка безпеки: спроба зробити неавторизований запит на `/api/exchange/trade` та перевірка отримання помилки `401 Unauthorized`.

### Верифікація деплою
1. DNS-перевірка: `ping btc.lexis.blog` має повертати IP-адресу Hostinger VPS.
2. Перевірка доступності сайту за адресою `https://btc.lexis.blog` з активним HTTPS-з'єднанням.
