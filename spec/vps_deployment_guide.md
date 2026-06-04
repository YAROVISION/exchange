# Покрокова інструкція розгортання Kronos на Hostinger VPS за допомогою Docker Compose та Traefik

Цей посібник описує процес розгортання додатку за допомогою **Docker Compose** під керуванням зворотного проксі-сервера **Traefik** (який автоматично керує SSL-сертифікатами Let's Encrypt через мітки контейнера) на вашому домені `btc.lexis.blog`.

---

## Крок 1. Підготовка бази даних у Supabase

1. Перейдіть до консолі [Supabase](https://supabase.com/) та відкрийте свій проект.
2. Перейдіть у розділ **SQL Editor** ліворуч та натисніть **New query**.
3. Скопіюйте та виконайте наступний SQL-код для створення таблиць та тригера для автоматичного балансу $100 при реєстрації користувача:

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

## Крок 2. Налаштування DNS

Перейдіть до керування DNS вашого домену `lexis.blog` та створіть запис:
* **Тип:** `A`
* **Ім'я (Host):** `btc` (або `btc.lexis.blog`)
* **Адреса (Points to):** `<IP-адреса_вашого_VPS>`

---

## Крок 3. Збірка та публікація образу в Docker Hub

Оскільки у вашому `docker-compose.yml` вказано використання готового образу:
`image: your-dockerhub-user/exchange-app:latest`

Вам необхідно зібрати образ локально (або на VPS) та опублікувати його в Docker Hub:

1. Авторизуйтесь у Docker Hub на робочій машині:
   ```bash
   docker login
   ```

2. Зберіть та надішліть образ (замініть `your-dockerhub-user` на ваше реальне ім'я користувача Docker Hub):
   ```bash
   # Перейдіть у кореневу папку проекту
   docker build -t your-dockerhub-user/exchange-app:latest .
   
   # Запуште образ у Docker Hub
   docker push your-dockerhub-user/exchange-app:latest
   ```

---

## Крок 4. Налаштування VPS та підготовка мережі Traefik

1. Підключіться до вашого VPS:
   ```bash
   ssh root@IP_адреса_вашого_VPS
   ```

2. Переконайтеся, що на VPS створено зовнішню мережу `traefik-proxy` (через яку Traefik спілкується з іншими контейнерами):
   ```bash
   docker network create traefik-proxy
   ```
   *(Якщо мережа вже була створена раніше, команда повідомить про це — це нормально).*

---

## Крок 5. Розгортання проекту на VPS через Docker Compose

1. Створіть робочу папку для додатку на VPS:
   ```bash
   mkdir -p /var/www/kronos
   cd /var/www/kronos
   ```

2. Створіть файл `docker-compose.yml`:
   ```bash
   nano docker-compose.yml
   ```
   Вставте туди конфігурацію (замініть `your-dockerhub-user` на ваше ім'я в Docker Hub):
   ```yaml
   version: '3.8'

   services:
     web:
       image: your-dockerhub-user/exchange-app:latest
       container_name: kronos-web
       env_file:
         - .env
       restart: unless-stopped
       labels:
         - traefik.enable=true
         - traefik.http.routers.kronos.rule=Host(`btc.lexis.blog`)
         - traefik.http.routers.kronos.entrypoints=websecure
         - traefik.http.routers.kronos.tls.certresolver=letsencrypt
         - traefik.http.services.kronos.loadbalancer.server.port=7070
       networks:
         - traefik-proxy

   networks:
     traefik-proxy:
       external: true
   ```

3. Створіть конфігураційний файл `.env` у тій же папці:
   ```bash
   nano .env
   ```
   Вставте параметри Supabase та Flask:
   ```env
   SUPABASE_URL=https://your-project-id.supabase.co
   SUPABASE_KEY=your-anon-or-service-role-key
   FLASK_SECRET_KEY=генеруйте_будь-який_випадковий_довгий_рядок
   ```

4. Запустіть додаток:
   ```bash
   # Завантажити найновіший образ та запустити контейнер
   docker compose pull && docker compose up -d
   ```

---

## Корисні команди для керування на VPS

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
* **Оновлення версії додатку після пушу в Docker Hub:**
  ```bash
  cd /var/www/kronos
  docker compose pull
  docker compose up -d
  ```
