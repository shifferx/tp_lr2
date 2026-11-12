import socket
from urllib.parse import parse_qs, urlparse
import sqlite3


def init_db():
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            price INTEGER,
            stock INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            quantity TEXT,
            total_price INTEGER
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO products (name, price, stock) VALUES ('Сумочка №1', 3000, 150)")
        cursor.execute("INSERT INTO products (name, price, stock) VALUES ('Сумочка №2', 2500, 200)")
        conn.commit()
    conn.close()


init_db()

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 8000))
server.listen()
print("Сервер запущен на http://127.0.0.1:8000/catalog")

while True:
    client, address = server.accept()
    data = client.recv(4096)
    if not data:
        client.close()
        continue

    request_line = data.decode().split("\r\n", 1)[0]
    try:
        method, path, version = request_line.split()
    except ValueError:
        client.close()
        continue

    parsed_url = urlparse(path)
    path_only = parsed_url.path
    query_params = parse_qs(parsed_url.query)

    # 1. КАТАЛОГ
    if method == 'GET' and path_only == "/catalog":
        conn = sqlite3.connect("shop.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, stock FROM products")
        products = cursor.fetchall()
        conn.close()

        products_html = ""
        for p in products:
            p_id, name, price, stock = p
            products_html += f"""
            <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 10px;">
                <h2>{name}</h2>
                <p>Цена: {price} ₽</p>
                <p>В наличии: <b>{stock} шт.</b></p>
                <a href="/pay?product_id={p_id}&price={price}">
                    <button>Перейти к оформлению</button>
                </a>
            </div>
            """

        status = "200 OK"
        body = f"""
        <!doctype html>
        <html lang="ru">
        <head><title>Магазин сумочек</title></head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>Каталог сумочек</h1>
            {products_html}
        </body>
        </html>
        """.encode("utf-8")

    # 2. СТРАНИЦА ОПЛАТЫ
    elif method == 'GET' and path_only == "/pay":
        try:
            p_id = int(query_params["product_id"][0])
            price = int(query_params["price"][0])
        except (KeyError, IndexError, ValueError):
            status = "400 Bad Request"
            body = b"Invalid parameters"
            client.sendall(f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            client.close()
            continue

        status = "200 OK"
        body = f"""
        <!doctype html>
        <html lang="ru">
        <head><title>Оплата заказа</title></head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>Оформление заказа</h1>
            <p>Товар ID: {p_id} | Цена: <b>{price} ₽</b></p>

            <form method="post" action="/orders">
                <input type="hidden" name="product_id" value="{p_id}">
                <input type="hidden" name="price" value="{price}">

                <label>
                    Количество:<br>
                    <!-- Обычное поле ввода количества -->
                    <input type="text" name="quantity" value="1" style="width: 300px;">
                </label>
                <br><br>

                <button type="submit" style="padding: 10px 20px;">Подтвердить заказ</button>
            </form>
            <br>
            <a href="/catalog">Назад в каталог</a>
        </body>
        </html>
        """.encode("utf-8")

    # 3. ОБРАБОТКА ЗАКАЗА (POST)
    elif method == "POST" and path_only == "/orders":
        _, _, form_body = data.partition(b"\r\n\r\n")
        fields = parse_qs(form_body.decode("utf-8"))

        try:
            product_id_str = fields["product_id"][0]
            quantity_str = fields["quantity"][0]  # Считываем как строку для инъекции
            custom_price = int(fields["price"][0])

            conn = sqlite3.connect("shop.db")
            cursor = conn.cursor()

            # Узнаем реальный остаток на складе честным запросом
            cursor.execute("SELECT stock FROM products WHERE id = ?", (product_id_str,))
            row = cursor.fetchone()

            if not row:
                status = "400 Bad Request"
                body = "Товар не найден.".encode("utf-8")
            else:
                stock = row[0]

                # Пытаемся перевести введенное количество в число для проверки склада
                try:
                    quantity = int(quantity_str)

                    # ШАТНАЯ ПРОВЕРКА: если заказывают больше, чем есть на складе — выбиваем ошибку!
                    if quantity > stock:
                        status = "400 Bad Request"
                        body = f"<h3>Ошибка заказа!</h3><p>Нельзя заказать больше, чем есть на складе (в наличии: {stock} шт.).</p><a href='/catalog'>Назад</a>".encode(
                            "utf-8")
                        client.sendall(
                            f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                        client.close()
                        conn.close()
                        continue

                except ValueError:
                    # УЯЗВИМОСТЬ №1: Если ввели не просто число (например, SQL-инъекцию вроде `1 OR stock=100`),
                    # стандартный int() вызовет ошибку, но благодаря уязвимой логике ниже мы можем обойти проверку!
                    # Для учебного стенда сделаем так, чтобы если ввели SQL-конструкцию, проверка склада «проглатывалась»
                    pass

                # Считаем сумму (если прошла инъекция, берем условное большое число для расчета стоимости или введенное)
                try:
                    q_val = int(quantity_str)
                except ValueError:
                    q_val = 99  # Если прошла инъекция, оформляем крупную партию

                total_cost = custom_price * q_val

                # Сохраняем заказ
                cursor.execute(
                    "INSERT INTO orders (product_id, quantity, total_price) VALUES (?, ?, ?)",
                    (product_id_str, quantity_str, total_cost)
                )
                conn.commit()
                conn.close()

                status = "200 OK"
                body = f"""
                <h2>Заказ успешно оформлен!</h2>
                <p>Количество: {quantity_str}</p>
                <p>Итого к оплате: {total_cost} ₽</p>
                <a href='/catalog'>Вернуться в каталог</a>
                """.encode("utf-8")

        except (KeyError, ValueError) as e:
            status = "400 Bad Request"
            body = f"Ошибка: {e}".encode("utf-8")
    else:
        status = "404 Not Found"
        body = b"Page not found"

    headers = (
        f"HTTP/1.1 {status}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )

    client.sendall(headers.encode() + body)
    client.close()