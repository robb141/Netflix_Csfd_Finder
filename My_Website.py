from flask import Flask, redirect, url_for, render_template
import sqlite3


app = Flask(__name__)



# @app.route('/')
# def home():
#     return render_template('index.html')


@app.route('/')
@app.route('/home')
def index():
    # conn = sqlite3.connect('movies.db')
    with sqlite3.connect('movies.db') as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM netflix;')
        return render_template('index.html', rows=c.fetchall())


if __name__ == '__main__':
    app.run(debug=True)


