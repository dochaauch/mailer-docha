from flask import Flask, render_template, request, redirect, session, url_for
import os
from mailer import load_clients_config, process_and_send_emails

app = Flask(__name__)
app.secret_key = os.urandom(24)  # можно заменить на свой ключ в .env

clients = load_clients_config()

@app.route('/', methods=['GET', 'POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        login = request.form['login']
        password = request.form['password']
        client = clients.get(login)

        if client and client['password'] == password:
            session['login'] = login
            return redirect(url_for('dashboard'))
        else:
            error = "Неверный логин или пароль"
    return render_template('login.html', error=error)


@app.route('/dashboard')
def dashboard():
    if 'login' not in session:
        return redirect(url_for('login'))
    client = clients[session['login']]
    return render_template('dashboard.html', client=client)


@app.route('/send', methods=['POST'])
def send():
    if 'login' not in session:
        return redirect(url_for('login'))
    client = clients[session['login']]
    result = process_and_send_emails(client)
    return render_template('result.html', result=result)


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)
