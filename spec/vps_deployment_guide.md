# Покрокова інструкція розгортання Kronos на Hostinger VPS у Docker-контейнері

Цей посібник містить повний набір інструкцій для запуску проекту з вашого GitHub-репозиторію на Hostinger VPS під доменом `btc.lexis.blog`.

---

## Крок 1. Підготовка бази даних у Supabase

Перш ніж розгортати додаток на сервері, необхідно створити необхідні таблиці та тригери в хмарі Supabase.

1. Перейдіть до консолі [Supabase](https://supabase.com/) та відкрийте свій проект.
2. Перейдіть у меню **SQL Editor** ліворуч та натисніть **New query**.
3. Скопіюйте та виконайте наступний SQL-скрипт для створення таблиць та тригера (для автоматичного створення гаманця на $100 при реєстрації користувача):

```sql
-- 1. Створення таблиці гаманців
CREATE TABLE public.wallets (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    usd_balance NUMERIC(20, 8) NOT NULL DEFAULT 100.00000000 CHECK (usd_balance >= 0),
    btc_balance NUMERIC(20, 8) NOT NULL DEFAULT 0.00000000 CHECK (btc_balance >= 0),
    avg_buy_price NUMERIC(20, 8) NOT NULL DEFAULT 0.00000000 CHECK (avg_buy_price >= 0),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Створення таблиці історії угод
CREATE TABLE public.trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type VARCHAR(10) NOT NULL CHECK (type IN ('buy', 'sell')),
    btc_amount NUMERIC(20, 8) NOT NULL CHECK (btc_amount > 0),
    price NUMERIC(20, 8) NOT NULL CHECK (price > 0),
    fee NUMERIC(20, 8) NOT NULL CHECK (fee >= 0),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_trades_user_id ON public.trades(user_id);

-- 3. Функція та тригер для автоматичного створення гаманця при реєстрації
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.wallets (user_id, usd_balance, btc_balance, avg_buy_price)
    VALUES (NEW.id, 100.00000000, 0.00000000, 0.00000000);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

---

## Крок 2. Налаштування DNS для домену

Щоб домен `btc.lexis.blog` вказував на ваш VPS:

1. Перейдіть до панелі керування DNS вашого домену `lexis.blog` (у Hostinger, Cloudflare або іншому реєстраторі).
2. Створіть новий запис:
   * **Тип (Type):** `A`
   * **Ім'я (Host):** `btc` (або `btc.lexis.blog`)
   * **Значення (Points to):** `IP_адреса_вашого_VPS`
   * **TTL:** за замовчуванням (або 3600)

---

## Крок 3. Підготовка VPS на Hostinger та встановлення Docker

Підключіться до вашого Hostinger VPS через SSH:
```bash
ssh root@IP_адреса_вашого_VPS
```

Якщо на сервері ще не встановлено Docker та Git, виконайте такі команди (для Ubuntu/Debian):

```bash
# Оновлення списку пакетів
sudo apt update && sudo apt upgrade -y

# Встановлення необхідних інструментів та Git
sudo apt install -y git curl apt-transport-https ca-certificates gnupg lsb-release

# Додавання офіційного GPG-ключа Docker
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Додавання репозиторію Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Встановлення Docker Engine
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Перевірка статусу Docker
sudo systemctl status docker
```

---

## Крок 4. Клонування репозиторію та конфігурація `.env`

1. Склонуйте репозиторій з GitHub у потрібну папку (наприклад, `/var/www/exchange`):
   ```bash
   sudo mkdir -p /var/www
   cd /var/www
   git clone https://github.com/YAROVISION/exchange.git
   cd exchange
   ```

2. Створіть файл конфігурації `.env` у кореневій директорії проекту (де розташовано `docker-compose.yml`):
   ```bash
   nano .env
   ```

3. Вставте наступні параметри (замініть на реальні URL та ключі вашого проекту Supabase):
   ```env
   SUPABASE_URL=https://your-project-id.supabase.co
   SUPABASE_KEY=your-anon-or-service-role-key
   FLASK_SECRET_KEY=генеруйте_будь-який_випадковий_довгий_рядок
   ```
   *(Для генерації випадкового ключа в Linux можна виконати команду `openssl rand -hex 24`)*.

4. Збережіть файл (`Ctrl + O`, потім `Enter` та `Ctrl + X`).

---

## Крок 5. Збірка та запуск контейнера через Docker Compose

1. Запустіть збірку Docker-образу та контейнер у фоновому режимі однією командою з кореневої папки проекту (де знаходиться `docker-compose.yml`):
   ```bash
   docker compose up -d
   ```
   *Ця команда сама зчитає змінні з файлу `.env`, збере образ та запустить контейнер `exchange-web` на порту `7070` з автоматичним перезапуском.*

2. Перевірте, що контейнер успішно запущено та працює:
   ```bash
   docker compose ps
   ```

---

## Крок 6. Встановлення та налаштування Nginx (Reverse Proxy)

Щоб перенаправляти HTTPS-запити з домену `btc.lexis.blog` на локальний порт Docker-контейнера (`7070`), налаштуємо Nginx:

1. Встановіть Nginx:
   ```bash
   sudo apt install -y nginx
   ```

2. Створіть файл конфігурації для вашого сайту:
   ```bash
   sudo nano /etc/nginx/sites-available/btc.lexis.blog
   ```

3. Додайте таку конфігурацію:
   ```nginx
   server {
       listen 80;
       server_name btc.lexis.blog;

       # Максимальний розмір завантажень
       client_max_body_size 50M;

       location / {
           proxy_pass http://127.0.0.1:7070;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded-for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

4. Збережіть файл. Увімкніть конфігурацію, створивши символічне посилання:
   ```bash
   sudo ln -s /etc/nginx/sites-available/btc.lexis.blog /etc/nginx/sites-enabled/
   ```

5. Перевірте конфігурацію Nginx на помилки та перезапустіть його:
   ```bash
   sudo nginx -t
   sudo systemctl restart nginx
   ```

---

## Крок 7. Отримання безкоштовного SSL сертифікату (HTTPS) через Certbot

Для захисту сайту та підключення HTTPS використаємо сертифікати Let's Encrypt:

1. Встановіть Certbot для Nginx:
   ```bash
   sudo apt install -y certbot python3-certbot-nginx
   ```

2. Запустіть процес отримання сертифікату:
   ```bash
   sudo certbot --nginx -d btc.lexis.blog
   ```
   *Далі введіть свій email для сповіщень та погодьтеся з умовами використання. Certbot автоматично оновить конфігурацію Nginx для перенаправлення всього трафіку на HTTPS (`http` -> `https`).*

3. Перевірте статус автоматичного оновлення сертифікатів:
   ```bash
   sudo systemctl status certbot.timer
   ```

---

## Корисні команди для керування додатком на VPS (через Docker Compose)

* **Перегляд логів роботи додатку:**
  ```bash
  docker compose logs -f
  ```
* **Перезапуск контейнера:**
  ```bash
  docker compose restart
  ```
* **Зупинка контейнера:**
  ```bash
  docker compose down
  ```
* **Оновлення коду проекту з GitHub:**
  ```bash
  cd /var/www/exchange
  git pull
  docker compose up -d --build
  ```
